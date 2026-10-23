"""模型调用的唯一入口（技术方案 9.3、9.6）。

所有模型调用（体验页、连通测试、以后的应用/入库/检索）都走这里，按顺序：
读模型 → 校验类型 → 校验接口地址 → 限流 → 解密密钥 → 调 LiteLLM → 记用量。

- 重试由这里自己做，只在还没收到任何片段时重试，不用 LiteLLM 的 `num_retries`。
- 两段超时：等第一个片段（非流式时等整个响应）用 `first_token_timeout_s`，
  整次调用（含全部重试）用 `total_timeout_s`。
- 每次调用结束（成功、失败、取消）用独立会话写一条 model_usage。
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any

import litellm
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.crypto import SecretDecryptError, decrypt_secret
from app.core.errors import (
    ModelRateLimitedError,
    ModelTypeMismatchError,
    ModelUnavailableError,
)
from app.core.net import check_model_host
from app.gateway import errors as gateway_errors
from app.gateway import thinking as thinking_map
from app.gateway import tokens
from app.gateway import usage as usage_recorder
from app.gateway.errors import ModelCallError
from app.gateway.events import ModelCallErrorCode
from app.gateway.ratelimit import RateLimited, RateLimiter
from app.models.tables import Model
from app.settings import service as settings_service

logger = logging.getLogger(__name__)

# 服务函数的公共依赖：会话工厂（读模型、写用量）和 Redis（限流）。
SessionMaker = async_sessionmaker[AsyncSession]


@dataclass
class CallMeta:
    """一次调用的发起人信息（技术方案 6.3：用量记录的快照）。"""

    source: str
    source_id: int | None = None
    user_id: int | None = None
    org_id: int | None = None


@dataclass
class TokenUsage:
    input_tokens: int | None
    output_tokens: int | None


@dataclass
class ChatChunk:
    """流式片段：文本、思考内容或用量，三者至多一个非空。"""

    text: str | None = None
    reasoning: str | None = None
    usage: TokenUsage | None = None


@dataclass
class ChatResult:
    text: str
    reasoning: str | None
    input_tokens: int | None
    output_tokens: int | None
    first_token_ms: int | None
    total_ms: int
    returned_model: str | None = None


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    input_tokens: int | None
    total_ms: int


@dataclass
class RerankItem:
    index: int
    score: float


@dataclass
class RerankResult:
    results: list[RerankItem]
    input_tokens: int | None
    total_ms: int


@dataclass
class _CallState:
    model: Model
    meta: CallMeta
    started: float
    first_token_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    ok: bool = False
    error_code: str | None = None


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


async def _load_model(
    sessionmaker: SessionMaker,
    model_id: int,
    expected_type: str,
    override_config: Model | None = None,
    allow_disabled: bool = False,
) -> tuple[Model, Any]:
    async with sessionmaker() as session:
        config = await settings_service.get_settings(session)
        if override_config is None:
            model = await session.get(Model, model_id)
        else:
            model = override_config
    if model is None or model.deleted_at is not None:
        raise ModelUnavailableError()
    if not allow_disabled and model.status != "enabled":
        raise ModelUnavailableError()
    if model.model_type != expected_type:
        raise ModelTypeMismatchError()
    await check_model_host(model.api_base, config.model_call.model_host_allowlist)
    return model, config


def _model_kwargs(model: Model) -> dict[str, Any]:
    """LiteLLM 的公共参数：模型 ID、接口地址、解密后的密钥。"""
    kwargs: dict[str, Any] = {"model": model.litellm_model}
    if model.api_base:
        kwargs["api_base"] = model.api_base
    if model.api_key_cipher:
        try:
            kwargs["api_key"] = decrypt_secret(model.api_key_cipher)
        except SecretDecryptError as exc:
            logger.error("模型密钥解密失败: model_id=%s", model.id)
            raise ModelCallError(ModelCallErrorCode.UPSTREAM_AUTH, "模型密钥无法解密") from exc
    return kwargs


def _merge_params(model: Model, params: dict[str, Any] | None) -> dict[str, Any]:
    """默认参数打底，请求参数覆盖；再处理 `thinking` 开关。"""
    merged: dict[str, Any] = dict(model.default_params or {})
    if params:
        merged.update(params)
    thinking = merged.pop("thinking", None)
    if thinking is not None and model.cap_thinking:
        merged.update(thinking_map.thinking_params(model.litellm_model, bool(thinking)))
    return merged


@asynccontextmanager
async def _call(
    sessionmaker: SessionMaker,
    redis: Redis,
    model_id: int,
    expected_type: str,
    meta: CallMeta,
    override_config: Model | None = None,
    allow_disabled: bool = False,
) -> AsyncIterator[_CallState]:
    """公共流程：读模型、校验、限流、记用量。"""
    model, config = await _load_model(
        sessionmaker, model_id, expected_type, override_config, allow_disabled
    )
    state = _CallState(model=model, meta=meta, started=monotonic())
    ingest = meta.source == "ingest"
    limiter = RateLimiter(redis, ingest_ratio=config.model_call.ingest_rate_ratio)
    recorded = False

    try:
        try:
            async with limiter.acquire(model, ingest=ingest):
                recorded = True
                try:
                    yield state
                    state.ok = True
                except ModelCallError as exc:
                    state.error_code = exc.error_code
                    raise
                except (asyncio.CancelledError, GeneratorExit):
                    state.error_code = ModelCallErrorCode.CANCELLED.value
                    raise
        except RateLimited as exc:
            if ingest:
                raise
            raise ModelRateLimitedError() from exc
    finally:
        if recorded:
            await usage_recorder.record(sessionmaker, _usage_record(state))


def _usage_record(state: _CallState) -> usage_recorder.UsageRecord:
    return usage_recorder.UsageRecord(
        model_id=state.model.id,
        model_name=state.model.name,
        user_id=state.meta.user_id,
        org_id=state.meta.org_id,
        source=state.meta.source,
        source_id=state.meta.source_id,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        first_token_ms=state.first_token_ms,
        total_ms=_ms(monotonic() - state.started),
        ok=state.ok,
        error_code=state.error_code,
    )


async def _close_stream(stream: Any) -> None:
    """关闭上游流，取消时也尽量完成。"""
    if stream is None:
        return
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    task = asyncio.ensure_future(aclose())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # 已经拿到取消，等关闭完成再继续传播。
        try:
            await task
        except Exception:  # noqa: BLE001 - 关闭失败只记日志
            logger.debug("关闭上游流失败", exc_info=True)
        raise
    except Exception:  # noqa: BLE001
        logger.debug("关闭上游流失败", exc_info=True)


def _extract_chunk(chunk: Any) -> tuple[str | None, str | None, TokenUsage | None]:
    text: str | None = None
    reasoning: str | None = None
    choices = getattr(chunk, "choices", None)
    if choices:
        delta = getattr(choices[0], "delta", None)
        if delta is not None:
            text = getattr(delta, "content", None)
            reasoning = getattr(delta, "reasoning_content", None)
    usage = None
    raw_usage = getattr(chunk, "usage", None)
    if raw_usage is not None:
        usage = TokenUsage(
            input_tokens=getattr(raw_usage, "prompt_tokens", None),
            output_tokens=getattr(raw_usage, "completion_tokens", None),
        )
    return text, reasoning, usage


async def _stream_once(
    model: Model,
    messages: list[dict],
    call_params: dict[str, Any],
    deadline: float,
    first_token_timeout: int,
) -> AsyncIterator[ChatChunk]:
    """一次上游流式调用；超时或上游异常时抛 `ModelCallError`。"""
    stream = None
    first_received = False
    try:
        kwargs = _model_kwargs(model)
        kwargs.update(call_params)
        kwargs.update({"stream": True, "stream_options": {"include_usage": True}})
        stream = await asyncio.wait_for(
            litellm.acompletion(messages=messages, **kwargs), timeout=first_token_timeout
        )
        iterator = stream.__aiter__()
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ModelCallError(ModelCallErrorCode.TOTAL_TIMEOUT, "调用超时")
            timeout = first_token_timeout if not first_received else remaining
            try:
                async with asyncio.timeout(timeout):
                    chunk = await iterator.__anext__()
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                if not first_received:
                    raise ModelCallError(
                        ModelCallErrorCode.FIRST_TOKEN_TIMEOUT, "连接超时"
                    ) from exc
                raise ModelCallError(ModelCallErrorCode.TOTAL_TIMEOUT, "调用超时") from exc
            text, reasoning, usage = _extract_chunk(chunk)
            if usage is not None:
                yield ChatChunk(usage=usage)
            if text:
                first_received = True
                yield ChatChunk(text=text)
            if reasoning:
                first_received = True
                yield ChatChunk(reasoning=reasoning)
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except ModelCallError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一映射成调用错误
        raise gateway_errors.map_exception(exc) from exc
    finally:
        await _close_stream(stream)


async def chat_stream(
    sessionmaker: SessionMaker,
    redis: Redis,
    meta: CallMeta,
    model_id: int,
    messages: list[dict],
    params: dict[str, Any] | None,
) -> AsyncIterator[ChatChunk]:
    """流式对话。产出文本/思考片段，最后产出一个用量片段。"""
    async with _call(sessionmaker, redis, model_id, "chat", meta) as state:
        model = state.model
        call_params = _merge_params(model, params)
        deadline = state.started + model.total_timeout_s
        first_token_timeout = model.first_token_timeout_s
        produced: list[str] = []
        last_usage: TokenUsage | None = None

        try:
            attempt = 0
            while True:
                attempt += 1
                received = False
                usage: TokenUsage | None = None
                try:
                    async for chunk in _stream_once(
                        model, messages, call_params, deadline, first_token_timeout
                    ):
                        if chunk.usage is not None:
                            usage = chunk.usage
                            last_usage = chunk.usage
                            continue
                        if not received:
                            received = True
                            state.first_token_ms = _ms(monotonic() - state.started)
                        if chunk.text:
                            produced.append(chunk.text)
                        yield chunk
                except ModelCallError as exc:
                    if received or not exc.retryable or attempt > model.max_retries:
                        raise
                    if deadline - monotonic() < first_token_timeout:
                        raise
                    continue

                state.input_tokens = (
                    usage.input_tokens
                    if usage and usage.input_tokens is not None
                    else tokens.count_messages(messages)
                )
                state.output_tokens = (
                    usage.output_tokens
                    if usage and usage.output_tokens is not None
                    else tokens.count_text("".join(produced))
                )
                yield ChatChunk(usage=TokenUsage(state.input_tokens, state.output_tokens))
                return
        except (asyncio.CancelledError, GeneratorExit):
            # 取消时取上游已返回的用量，没有就按已产出内容估算（技术方案 9.6）。
            if last_usage is not None:
                if last_usage.input_tokens is not None:
                    state.input_tokens = last_usage.input_tokens
                if last_usage.output_tokens is not None:
                    state.output_tokens = last_usage.output_tokens
            if state.input_tokens is None:
                state.input_tokens = tokens.count_messages(messages)
            if state.output_tokens is None:
                state.output_tokens = tokens.count_text("".join(produced))
            raise


async def _complete_once(
    model: Model,
    messages: list[dict],
    call_params: dict[str, Any],
    timeout: int,
) -> Any:
    kwargs = _model_kwargs(model)
    kwargs.update(call_params)
    try:
        return await asyncio.wait_for(
            litellm.acompletion(messages=messages, **kwargs), timeout=timeout
        )
    except TimeoutError as exc:
        raise ModelCallError(ModelCallErrorCode.FIRST_TOKEN_TIMEOUT, "连接超时") from exc
    except ModelCallError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise gateway_errors.map_exception(exc) from exc


def _extract_message(response: Any) -> tuple[str, str | None]:
    choices = getattr(response, "choices", None)
    if not choices:
        return "", None
    message = getattr(choices[0], "message", None)
    if message is None:
        return "", None
    text = getattr(message, "content", None) or ""
    reasoning = getattr(message, "reasoning_content", None)
    return text, reasoning


def _extract_usage(response: Any) -> TokenUsage | None:
    raw = getattr(response, "usage", None)
    if raw is None:
        return None
    return TokenUsage(
        input_tokens=getattr(raw, "prompt_tokens", None),
        output_tokens=getattr(raw, "completion_tokens", None),
    )


async def chat(
    sessionmaker: SessionMaker,
    redis: Redis,
    meta: CallMeta,
    model_id: int,
    messages: list[dict],
    params: dict[str, Any] | None,
    *,
    override_config: Model | None = None,
    allow_disabled: bool = False,
) -> ChatResult:
    """非流式对话（第四期意图识别、改写用）。"""
    async with _call(
        sessionmaker,
        redis,
        model_id,
        "chat",
        meta,
        override_config=override_config,
        allow_disabled=allow_disabled,
    ) as state:
        model = state.model
        call_params = _merge_params(model, params)
        deadline = state.started + model.total_timeout_s
        timeout = model.first_token_timeout_s

        attempt = 0
        while True:
            attempt += 1
            try:
                response = await _complete_once(model, messages, call_params, timeout)
            except ModelCallError as exc:
                if not exc.retryable or attempt > model.max_retries:
                    raise
                if deadline - monotonic() < timeout:
                    raise
                continue

            text, reasoning = _extract_message(response)
            usage = _extract_usage(response)
            state.first_token_ms = _ms(monotonic() - state.started)
            state.input_tokens = (
                usage.input_tokens
                if usage and usage.input_tokens is not None
                else tokens.count_messages(messages)
            )
            state.output_tokens = (
                usage.output_tokens
                if usage and usage.output_tokens is not None
                else tokens.count_text(text)
            )
            return ChatResult(
                text=text,
                reasoning=reasoning,
                input_tokens=state.input_tokens,
                output_tokens=state.output_tokens,
                first_token_ms=state.first_token_ms,
                total_ms=_ms(monotonic() - state.started),
                returned_model=getattr(response, "model", None),
            )


async def embed(
    sessionmaker: SessionMaker,
    redis: Redis,
    meta: CallMeta,
    model_id: int,
    texts: Sequence[str],
    *,
    override_config: Model | None = None,
    allow_disabled: bool = False,
) -> EmbedResult:
    """向量化。"""
    async with _call(
        sessionmaker,
        redis,
        model_id,
        "embedding",
        meta,
        override_config=override_config,
        allow_disabled=allow_disabled,
    ) as state:
        model = state.model
        deadline = state.started + model.total_timeout_s
        timeout = model.first_token_timeout_s
        inputs = list(texts)

        attempt = 0
        while True:
            attempt += 1
            try:
                response = await _embedding_once(model, inputs, timeout)
            except ModelCallError as exc:
                if not exc.retryable or attempt > model.max_retries:
                    raise
                if deadline - monotonic() < timeout:
                    raise
                continue

            vectors = [_embedding_vector(item) for item in response.data]
            usage = _extract_usage(response)
            state.input_tokens = (
                usage.input_tokens
                if usage and usage.input_tokens is not None
                else tokens.count_text("\n".join(inputs))
            )
            return EmbedResult(
                vectors=vectors,
                input_tokens=state.input_tokens,
                total_ms=_ms(monotonic() - state.started),
            )


async def _embedding_once(model: Model, inputs: list[str], timeout: int) -> Any:
    kwargs = _model_kwargs(model)
    kwargs.update(model.default_params or {})
    try:
        return await asyncio.wait_for(litellm.aembedding(input=inputs, **kwargs), timeout=timeout)
    except TimeoutError as exc:
        raise ModelCallError(ModelCallErrorCode.FIRST_TOKEN_TIMEOUT, "连接超时") from exc
    except ModelCallError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise gateway_errors.map_exception(exc) from exc


def _embedding_vector(item: Any) -> list[float]:
    if isinstance(item, dict):
        return [float(value) for value in item.get("embedding", [])]
    return [float(value) for value in item.embedding]


async def rerank(
    sessionmaker: SessionMaker,
    redis: Redis,
    meta: CallMeta,
    model_id: int,
    query: str,
    documents: Sequence[str],
    top_n: int | None = None,
    *,
    override_config: Model | None = None,
    allow_disabled: bool = False,
) -> RerankResult:
    """重排。"""
    async with _call(
        sessionmaker,
        redis,
        model_id,
        "rerank",
        meta,
        override_config=override_config,
        allow_disabled=allow_disabled,
    ) as state:
        model = state.model
        deadline = state.started + model.total_timeout_s
        timeout = model.first_token_timeout_s
        docs = list(documents)

        attempt = 0
        while True:
            attempt += 1
            try:
                response = await _rerank_once(model, query, docs, top_n, timeout)
            except ModelCallError as exc:
                if not exc.retryable or attempt > model.max_retries:
                    raise
                if deadline - monotonic() < timeout:
                    raise
                continue

            state.input_tokens = tokens.count_text(query) + sum(
                tokens.count_text(doc) for doc in docs
            )
            return RerankResult(
                results=[_rerank_item(item) for item in response.results],
                input_tokens=state.input_tokens,
                total_ms=_ms(monotonic() - state.started),
            )


async def _rerank_once(
    model: Model, query: str, docs: list[str], top_n: int | None, timeout: int
) -> Any:
    kwargs = _model_kwargs(model)
    kwargs.update(model.default_params or {})
    if top_n is not None:
        kwargs["top_n"] = top_n
    try:
        return await asyncio.wait_for(
            litellm.arerank(query=query, documents=docs, **kwargs), timeout=timeout
        )
    except TimeoutError as exc:
        raise ModelCallError(ModelCallErrorCode.FIRST_TOKEN_TIMEOUT, "连接超时") from exc
    except ModelCallError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise gateway_errors.map_exception(exc) from exc


def _rerank_item(item: Any) -> RerankItem:
    if isinstance(item, dict):
        return RerankItem(index=int(item["index"]), score=float(item["relevance_score"]))
    return RerankItem(index=int(item.index), score=float(item.relevance_score))
