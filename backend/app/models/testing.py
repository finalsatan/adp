"""连通测试：已保存模型与保存前（表单参数）的测试调用（P2-06，技术方案 9.4）。

- 对话模型：发送“你好”，`max_tokens=16`，关闭深度思考（模型支持时）。
- 向量化模型：向量化一个短句，返回维度与登记的 `embedding_dim` 不一致时 `DIM_MISMATCH`。
- 重排模型：对两个短文本重排。
- 整个测试 10 秒超时、不重试（需求 3.4“连接超时（10 秒）”，补充：测试要快速给出结论）。

调用一律经 P2-05 的调用层（`source=model_test`，发起人为操作人），复用同一套
限流、错误映射与用量记录；用 `override_config` 传入临时配置，而不是另写调用逻辑。
调用失败不是接口错误：这里返回 `ok=false` 的结果，由路由以 200 返回。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.gateway import service as gateway
from app.gateway.errors import ModelCallError
from app.gateway.events import ModelCallErrorCode
from app.gateway.service import CallMeta, SessionMaker
from app.iam.context import Ctx
from app.models import service as models_service
from app.models.schemas import ModelTestIn, ModelTestResult, ModelType
from app.models.tables import Model

# 需求 3.4：连通测试整个调用 10 秒超时；不重试，保证快速给出结论。
TEST_TIMEOUT_S = 10
CHAT_PROMPT = "你好"
CHAT_MAX_TOKENS = 16
EMBED_TEXT = "连通测试"
RERANK_QUERY = "连通测试"
RERANK_DOCUMENTS = ["这是第一段文本", "这是第二段文本"]


@dataclass
class _Outcome:
    """一次测试的内部结果，最后转成 `ModelTestResult`。"""

    ok: bool
    latency_ms: int = 0
    first_token_ms: int | None = None
    reply: str | None = None
    returned_model: str | None = None
    dims: int | None = None
    error_code: str | None = None
    error_message: str | None = None


def call_meta(ctx: Ctx) -> CallMeta:
    """连通测试的用量记录来源固定为 `model_test`，发起人为操作人。"""
    return CallMeta(
        source="model_test",
        source_id=None,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
    )


def _clone_config(config: Model) -> Model:
    """复制一份临时配置并套用测试固定设置（10 秒超时、不重试）。

    临时对象不加入任何会话，不会落库；`id` 保留，让用量记录归到被测模型。
    """
    columns = {column.key for column in sa.inspect(Model).columns}
    values = {key: getattr(config, key) for key in columns}
    values.update(
        first_token_timeout_s=TEST_TIMEOUT_S,
        total_timeout_s=TEST_TIMEOUT_S,
        max_retries=0,
    )
    return Model(**values)


def _form_config(body: ModelTestIn, api_key_cipher: bytes | None) -> Model:
    """把表单参数组装成一份临时模型配置（不落库）。"""
    return Model(
        id=body.model_id,
        name=body.name,
        status="enabled",
        model_type=body.model_type.value,
        litellm_model=body.litellm_model,
        api_base=body.api_base,
        api_key_cipher=api_key_cipher,
        default_params=body.default_params,
        cap_thinking=body.cap_thinking,
        embedding_dim=body.embedding_dim,
    )


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


async def _run(
    sessionmaker: SessionMaker,
    redis: Redis,
    meta: CallMeta,
    config: Model,
    expected_dim: int | None,
    allow_disabled: bool = False,
) -> _Outcome:
    """按模型类型跑一次测试，把 `ModelCallError` 转成 `ok=false`。"""
    started = monotonic()
    test_config = _clone_config(config)
    try:
        if config.model_type == ModelType.chat.value:
            result = await gateway.chat(
                sessionmaker,
                redis,
                meta,
                test_config.id,
                [{"role": "user", "content": CHAT_PROMPT}],
                {"max_tokens": CHAT_MAX_TOKENS, "thinking": False},
                override_config=test_config,
                allow_disabled=allow_disabled,
            )
            outcome = _Outcome(
                ok=True,
                first_token_ms=result.first_token_ms,
                reply=result.text,
                returned_model=result.returned_model,
            )
        elif config.model_type == ModelType.embedding.value:
            result = await gateway.embed(
                sessionmaker,
                redis,
                meta,
                test_config.id,
                [EMBED_TEXT],
                override_config=test_config,
                allow_disabled=allow_disabled,
            )
            dims = len(result.vectors[0]) if result.vectors else 0
            if expected_dim is not None and dims != expected_dim:
                outcome = _Outcome(
                    ok=False,
                    dims=dims,
                    error_code=ModelCallErrorCode.DIM_MISMATCH.value,
                    error_message=f"返回维度 {dims}，与登记的 {expected_dim} 不一致",
                )
            else:
                outcome = _Outcome(ok=True, dims=dims)
        else:
            await gateway.rerank(
                sessionmaker,
                redis,
                meta,
                test_config.id,
                RERANK_QUERY,
                RERANK_DOCUMENTS,
                override_config=test_config,
                allow_disabled=allow_disabled,
            )
            outcome = _Outcome(ok=True)
    except ModelCallError as exc:
        outcome = _Outcome(ok=False, error_code=exc.error_code, error_message=exc.message)

    outcome.latency_ms = _ms(monotonic() - started)
    return outcome


def _to_result(outcome: _Outcome) -> ModelTestResult:
    return ModelTestResult(
        ok=outcome.ok,
        latency_ms=outcome.latency_ms,
        first_token_ms=outcome.first_token_ms,
        reply=outcome.reply,
        returned_model=outcome.returned_model,
        dims=outcome.dims,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        tested_at=datetime.now(UTC),
    )


async def test_saved_model(
    session: AsyncSession,
    sessionmaker: SessionMaker,
    redis: Redis,
    meta: CallMeta,
    model: Model,
) -> ModelTestResult:
    """测试已保存的模型（停用也可测），结果写回 `last_test_*`。"""
    outcome = await _run(sessionmaker, redis, meta, model, model.embedding_dim, allow_disabled=True)
    model.last_test_at = datetime.now(UTC)
    model.last_test_ok = outcome.ok
    model.last_test_message = None if outcome.ok else outcome.error_message
    await session.commit()
    return _to_result(outcome)


async def test_form(
    session: AsyncSession,
    sessionmaker: SessionMaker,
    redis: Redis,
    meta: CallMeta,
    body: ModelTestIn,
) -> ModelTestResult:
    """用表单参数测试，不落库；`api_key` 为空且带 `model_id` 时用已保存的密钥。"""
    api_key_cipher: bytes | None = None
    if body.api_key:
        api_key_cipher = encrypt_secret(body.api_key)
    elif body.model_id is not None:
        saved = await models_service.get_model(session, body.model_id)
        api_key_cipher = saved.api_key_cipher

    config = _form_config(body, api_key_cipher)
    outcome = await _run(sessionmaker, redis, meta, config, body.embedding_dim)
    return _to_result(outcome)
