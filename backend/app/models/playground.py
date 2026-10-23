"""模型体验（SSE）的业务逻辑（技术方案 9.6）。

体验页不保存对话：`messages` 原样作为历史，`params` 覆盖模型默认参数，
`max_tokens` 超过模型 `max_output` 时截断。事件顺序 RUN_STARTED → 文本/思考
片段 → RUN_FINISHED；出错以 RUN_ERROR 结束。用量来源 `playground`。
"""

import uuid
from collections.abc import AsyncIterator
from time import monotonic
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ModelTypeMismatchError, ModelUnavailableError
from app.gateway import service as gateway
from app.gateway.errors import ModelCallError
from app.gateway.events import (
    ReasoningDeltaEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    SseEvent,
    TextDeltaEvent,
)
from app.gateway.service import CallMeta
from app.gateway.sse import sse_response
from app.iam.context import Ctx
from app.models.schemas import PlaygroundChatIn
from app.models.tables import Model

SOURCE = "playground"
SessionMaker = async_sessionmaker[AsyncSession]


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


async def _load_chat_model(sessionmaker: SessionMaker, model_id: int) -> Model:
    """流式开始前的检查：模型必须存在、已启用且是对话类型。"""
    async with sessionmaker() as session:
        model = await session.get(Model, model_id)
    if model is None or model.deleted_at is not None or model.status != "enabled":
        raise ModelUnavailableError()
    if model.model_type != "chat":
        raise ModelTypeMismatchError()
    return model


def _build_params(body: PlaygroundChatIn, model: Model) -> dict[str, Any]:
    """请求参数去掉未填项；`max_tokens` 超过 `max_output` 时截断。"""
    params = body.params.model_dump(exclude_none=True)
    max_tokens = params.get("max_tokens")
    if max_tokens is not None and model.max_output is not None and max_tokens > model.max_output:
        params["max_tokens"] = model.max_output
    return params


async def _events(
    sessionmaker: SessionMaker,
    redis: Redis,
    meta: CallMeta,
    model_id: int,
    messages: list[dict],
    params: dict[str, Any],
) -> AsyncIterator[SseEvent]:
    """把 gateway 的片段流翻译成 SSE 事件流。"""
    yield RunStartedEvent(run_id=uuid.uuid4().hex)
    started = monotonic()
    first_token_ms: int | None = None
    input_tokens = 0
    output_tokens = 0
    try:
        async for chunk in gateway.chat_stream(
            sessionmaker, redis, meta, model_id, messages, params
        ):
            if chunk.usage is not None:
                input_tokens = chunk.usage.input_tokens or 0
                output_tokens = chunk.usage.output_tokens or 0
                continue
            if first_token_ms is None:
                first_token_ms = _ms(monotonic() - started)
            if chunk.text:
                yield TextDeltaEvent(delta=chunk.text)
            if chunk.reasoning:
                yield ReasoningDeltaEvent(delta=chunk.reasoning)
    except ModelCallError as exc:
        # 调用错误代码直接作为 RUN_ERROR 的 code。
        yield RunErrorEvent(code=exc.error_code, message=exc.message)
        return
    except AppError as exc:
        # 流开始后才发生的限流等，统一以事件返回。
        yield RunErrorEvent(code=exc.code, message=exc.message)
        return
    yield RunFinishedEvent(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        first_token_ms=first_token_ms or 0,
        total_ms=_ms(monotonic() - started),
    )


async def chat(
    request: Request,
    sessionmaker: SessionMaker,
    redis: Redis,
    ctx: Ctx,
    model_id: int,
    body: PlaygroundChatIn,
) -> StreamingResponse:
    """模型体验对话：预检查后返回 SSE 响应。"""
    model = await _load_chat_model(sessionmaker, model_id)
    meta = CallMeta(
        source=SOURCE,
        source_id=None,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
    )
    messages = [message.model_dump() for message in body.messages]
    params = _build_params(body, model)
    events = _events(sessionmaker, redis, meta, model_id, messages, params)
    return sse_response(events, request)
