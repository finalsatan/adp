"""流式回答、停止与断开、重新生成与会话生成锁（应用技术方案 9、技术方案 9.6）。

调用 P4-06 的流程编排，把步骤与文本事件转发成 SSE；客户端断开时取消流程任务，
在 `finally` 里用**独立的数据库会话** + `asyncio.shield` 保存已输出的部分并把状态
记为 `stopped`（9.2）。会话生成锁用 P2-04 的并发租约脚本，键 `conv_gen:{会话 ID}`，
60 秒租约、生成期间每 20 秒续约，已被占用返回 409 `CONVERSATION_BUSY`。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from fastapi import Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from app.apps.config import KbQaAppConfig
from app.apps.tables import App, AppVersion
from app.chat import conversations
from app.chat.citations import build_citations, citations_for_event
from app.chat.events import (
    ChatRunFinishedEvent,
    ChatRunStartedEvent,
    ChatSseEvent,
    CitationsEvent,
    StepStartedEvent,
)
from app.chat.pipeline import AnswerRun, EmitFn, run_flow
from app.chat.prompts import HistoryTurn
from app.chat.schemas import (
    AnswerStatus,
    StepName,
)
from app.chat.tables import ChatAnswer, ChatTurn
from app.core.errors import AppError, ConversationBusyError
from app.gateway.errors import ModelCallError
from app.gateway.events import RunErrorEvent
from app.gateway.service import SessionMaker
from app.gateway.sse import sse_response

logger = logging.getLogger(__name__)

# 会话生成锁：60 秒租约、每 20 秒续约（应用技术方案 9.3、技术方案 9.3）。
CONV_LOCK_LEASE_SECONDS = 60.0
CONV_LOCK_RENEW_INTERVAL = 20.0

_LUA_DIR = Path(__file__).resolve().parents[1] / "gateway" / "lua"
_ACQUIRE_LUA = (_LUA_DIR / "acquire.lua").read_text(encoding="utf-8")
_RENEW_LUA = (_LUA_DIR / "renew.lua").read_text(encoding="utf-8")


# ---- 会话生成锁 ----


class ConversationLock:
    """一次已获取的会话生成租约；`release()` 停续约并删除成员。"""

    def __init__(
        self,
        redis: Redis,
        key: str,
        member_id: str,
        *,
        lease_seconds: float,
        renew_interval: float,
    ) -> None:
        self.redis = redis
        self.key = key
        self.member_id = member_id
        self.lease_seconds = lease_seconds
        self.renew_interval = renew_interval
        self.renew_task: asyncio.Task[None] | None = None

    async def renew(self) -> None:
        await self.redis.eval(_RENEW_LUA, 1, self.key, self.member_id, str(self.lease_seconds))

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(self.renew_interval)
            try:
                await self.renew()
            except Exception:  # noqa: BLE001 - 续约失败只记日志，不打断生成
                logger.warning("会话生成锁续约失败", exc_info=True)

    async def _finish(self) -> None:
        if self.renew_task is not None:
            self.renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.renew_task
        await self.redis.zrem(self.key, self.member_id)

    async def release(self) -> None:
        """停止续约并删除成员；清理本身不能被取消打断。"""
        finish = asyncio.ensure_future(self._finish())
        try:
            await asyncio.shield(finish)
        except asyncio.CancelledError:
            await finish
            raise


async def acquire_conversation_lock(
    redis: Redis,
    conversation_id: int,
    *,
    lease_seconds: float = CONV_LOCK_LEASE_SECONDS,
    renew_interval: float = CONV_LOCK_RENEW_INTERVAL,
) -> ConversationLock:
    """取会话生成锁；已被占用抛 `ConversationBusyError`（409）。"""
    key = f"conv_gen:{conversation_id}"
    member_id = uuid4().hex
    # acquire.lua：0 个 QPS 检查、1 个并发检查、上限 1（应用技术方案 9.3）。
    result = await redis.eval(_ACQUIRE_LUA, 1, key, "0", "1", member_id, str(lease_seconds), "1")
    if int(result[0]) != 1:
        raise ConversationBusyError()
    lock = ConversationLock(
        redis,
        key,
        member_id,
        lease_seconds=lease_seconds,
        renew_interval=renew_interval,
    )
    lock.renew_task = asyncio.create_task(lock._renew_loop())
    return lock


# ---- 事件流 ----


def _error_code(error: Exception) -> str:
    if isinstance(error, AppError):
        return error.code
    if isinstance(error, ModelCallError):
        return error.error_code
    return type(error).__name__


# ---- 事件流 ----


async def _save(run: AnswerRun) -> None:
    """独立会话保存；取消时等保存完成再继续传播取消（9.2）。"""
    task = asyncio.ensure_future(conversations.save_answer_result(run))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise


async def _run_and_finish(
    run: AnswerRun, emit: EmitFn, queue: asyncio.Queue[ChatSseEvent | None]
) -> None:
    """执行流程，把结束 / 出错事件放进队列，最后无论成败都保存结果。"""
    try:
        await run_flow(run, emit)
    except asyncio.CancelledError:
        run.status = AnswerStatus.stopped
        raise
    except (AppError, ModelCallError) as exc:
        # run_flow 已把状态置为 failed、异常写进 run.error。
        await queue.put(RunErrorEvent(code=_error_code(exc), message=str(exc) or exc.code))
    else:
        await queue.put(
            ChatRunFinishedEvent(
                kind=run.kind,
                status=run.status,
                input_tokens=run.input_tokens or 0,
                output_tokens=run.output_tokens or 0,
                first_token_ms=run.first_token_ms or 0,
                total_ms=run.total_ms or 0,
            )
        )
    finally:
        if run.status == AnswerStatus.generating:
            run.status = AnswerStatus.failed
        try:
            await _save(run)
        finally:
            with suppress(Exception):
                await queue.put(None)


async def _events(run: AnswerRun, lock: ConversationLock) -> AsyncIterator[ChatSseEvent]:
    """RUN_STARTED 之后转发流程事件；结束 / 断线时释放会话生成锁。"""
    queue: asyncio.Queue[ChatSseEvent | None] = asyncio.Queue()
    citations_sent = False

    async def emit(event: ChatSseEvent) -> None:
        nonlocal citations_sent
        if (
            not citations_sent
            and isinstance(event, StepStartedEvent)
            and event.step == StepName.generate
        ):
            run.citations = build_citations(run)
            citations_sent = True
            if run.citations:
                await queue.put(CitationsEvent(citations=citations_for_event(run.citations)))
        await queue.put(event)

    try:
        yield ChatRunStartedEvent(
            run_id=uuid4().hex,
            conversation_id=run.conversation_id or 0,
            turn_id=run.turn_id or 0,
            answer_id=run.answer_id,
        )
        runner = asyncio.create_task(_run_and_finish(run, emit, queue))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not runner.done():
                runner.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await runner
    finally:
        await lock.release()


# ---- 入口 ----


def answer_stream(
    request: Request,
    *,
    sessionmaker: SessionMaker,
    redis: Redis,
    lock: ConversationLock,
    app: App,
    version: AppVersion,
    config: KbQaAppConfig,
    conversation_id: int,
    turn: ChatTurn,
    answer: ChatAnswer,
    question: str,
    history: list[HistoryTurn],
    user_id: int,
    org_id: int | None,
) -> StreamingResponse:
    """构造一次提问 / 重新生成的 AnswerRun 并返回 SSE 响应。"""
    run = AnswerRun(
        app_id=app.id,
        config=config,
        question=question,
        sessionmaker=sessionmaker,
        redis=redis,
        conversation_id=conversation_id,
        turn_id=turn.id,
        answer_id=answer.id,
        version_id=version.id,
        user_id=user_id,
        org_id=org_id,
        source="app",
        history=history,
    )
    return sse_response(_events(run, lock), request)


__all__ = [
    "ConversationLock",
    "acquire_conversation_lock",
    "answer_stream",
    "build_citations",
    "citations_for_event",
]
