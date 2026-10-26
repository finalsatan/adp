"""调试预览（应用技术方案 12.1、8.3、14）。

- `POST /apps/{app}/debug/chat`：用当前草稿配置跑一遍回答流程，SSE 返回与对话页
  相同的事件，最后多一个 `TRACE`；调试会话不保存（不写 `conversations` /
  `chat_turns` / `chat_answers` / `answer_traces`），用量来源记为 `app_debug`。
- `GET /apps/{app}/debug/chunks/{chunk}/preview`：搭建者点击召回结果看原文位置，
  只放行草稿引用的知识库，复用 P4-10 的预览函数（整份 / 被引用部分按本人对
  知识库的查看权限决定）。

流程本身复用 P4-06 的 `run_flow`、P4-08 的历史组装、P4-09 的事件模型与
`sse_response`、P4-10 的引用快照与预览，不另写一套。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import uuid4

from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import KbQaAppConfig
from app.apps.schemas import PublishProblemOut
from app.apps.tables import App
from app.apps.validation import parse_config
from app.chat.citations import (
    build_citations,
    can_view_whole,
    citations_for_event,
    render_preview,
)
from app.chat.events import (
    ChatRunFinishedEvent,
    ChatRunStartedEvent,
    ChatSseEvent,
    CitationsEvent,
    StepStartedEvent,
    TraceEvent,
)
from app.chat.pipeline import AnswerRun, EmitFn, run_flow
from app.chat.prompts import HistoryTurn, history_from_messages
from app.chat.schemas import (
    CitationOut,
    CitationType,
    DebugChatIn,
    StepName,
)
from app.core.errors import (
    AppError,
    DraftConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.storage import Storage
from app.gateway.errors import ModelCallError
from app.gateway.events import RunErrorEvent
from app.gateway.service import SessionMaker
from app.gateway.sse import sse_response
from app.iam.context import Ctx
from app.kb.schemas import FileType
from app.kb.tables import Chunk, Document
from app.resources import access
from app.resources.tables import Resource

APP_TYPE = "app"
# 草稿配置不完整的问题码（应用技术方案 12.1；复用发布校验的命名）。
MODEL_REQUIRED = "MODEL_REQUIRED"
KB_REQUIRED = "KB_REQUIRED"


def _missing_problems(config: KbQaAppConfig) -> list[PublishProblemOut]:
    """调试只要求“选了模型、选了知识库”，其余问题留到运行时或发布校验（12.1）。"""
    problems: list[PublishProblemOut] = []
    if config.model is None or config.model.chat_model_id is None:
        problems.append(
            PublishProblemOut(
                code=MODEL_REQUIRED, message="请选择回答模型", field="model.chat_model_id"
            )
        )
    if not config.knowledge.kbs:
        problems.append(
            PublishProblemOut(
                code=KB_REQUIRED, message="请至少引用一个知识库", field="knowledge.kbs"
            )
        )
    return problems


async def prepare_run(
    session: AsyncSession, ctx: Ctx, app_id: int, body: DebugChatIn
) -> tuple[App, KbQaAppConfig, list[HistoryTurn]]:
    """权限、草稿版本、配置完整性与历史裁剪；不通过时抛对应错误。"""
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    app = await session.get(App, app_id)
    if app is None:
        raise NotFoundError()
    if app.draft_revision != body.draft_revision:
        raise DraftConflictError()
    config = parse_config(app.app_type, app.draft_config)
    problems = _missing_problems(config)
    if problems:
        raise ValidationError(detail={"problems": [problem.model_dump() for problem in problems]})
    history = history_from_messages(body.history, memory_turns=config.memory_turns)
    return app, config, history


def _error_code(error: Exception) -> str:
    if isinstance(error, AppError):
        return error.code
    if isinstance(error, ModelCallError):
        return error.error_code
    return type(error).__name__


# ---- 调试 SSE ----


async def _run_and_finish(
    run: AnswerRun, emit: EmitFn, queue: asyncio.Queue[ChatSseEvent | None]
) -> None:
    """执行流程；结束后发结束 / 出错事件，最后发 `TRACE`，不保存任何结果。"""
    try:
        await run_flow(run, emit)
    except asyncio.CancelledError:
        raise
    except (AppError, ModelCallError) as exc:
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
        # 调试预览最后多一个 TRACE：完整的处理过程（12.1、9.1）。
        with suppress(Exception):
            await queue.put(TraceEvent(trace=run.trace.build()))
        with suppress(Exception):
            await queue.put(None)


async def _events(run: AnswerRun) -> AsyncIterator[ChatSseEvent]:
    """RUN_STARTED 之后转发流程事件；生成开始前发 CITATIONS（同对话页）。"""
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

    yield ChatRunStartedEvent(run_id=uuid4().hex, conversation_id=0, turn_id=0, answer_id=0)
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


def debug_stream(
    request: Request,
    *,
    sessionmaker: SessionMaker,
    redis: Redis,
    app: App,
    config: KbQaAppConfig,
    question: str,
    history: list[HistoryTurn],
    user_id: int,
    org_id: int | None,
) -> StreamingResponse:
    """构造一次调试运行的 `AnswerRun`（没有会话 / 轮次 / 回答 ID）并返回 SSE 响应。"""
    run = AnswerRun(
        app_id=app.id,
        config=config,
        question=question,
        sessionmaker=sessionmaker,
        redis=redis,
        source="app_debug",
        history=history,
        user_id=user_id,
        org_id=org_id,
    )
    return sse_response(_events(run), request)


# ---- 调试分块预览 ----


def _chunk_citation(chunk: Chunk, doc: Document, kb_name: str) -> CitationOut:
    """把一个分块拼成 P4-10 预览函数要的引用快照（12.1、8.3）。"""
    return CitationOut(
        n=1,
        type=CitationType.doc,
        kb_id=chunk.kb_id,
        kb_name=kb_name,
        score=0.0,
        doc_id=doc.id,
        doc_name=doc.name,
        file_type=FileType(doc.file_type),
        chunk_ids=[chunk.id],
        generation=chunk.generation,
        content=chunk.content,
        positions=chunk.positions or [],
        edited=chunk.edited,
    )


async def preview_chunk(
    session: AsyncSession, storage: Storage, ctx: Ctx, app_id: int, chunk_id: int
) -> Response:
    """调试里点击分块看原文位置（12.1、8.3）。

    要求应用“编辑”权限，并且分块所在的知识库被草稿引用；否则 404。整份原文还是
    只看被引用部分，按本人对知识库的查看权限决定。
    """
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    app = await session.get(App, app_id)
    if app is None:
        raise NotFoundError()
    config = parse_config(app.app_type, app.draft_config)
    chunk = await session.get(Chunk, chunk_id)
    if chunk is None:
        raise NotFoundError()
    if chunk.kb_id not in {ref.kb_id for ref in config.knowledge.kbs}:
        raise NotFoundError()
    doc = await session.get(Document, chunk.doc_id)
    if doc is None:
        raise NotFoundError()
    kb_resource = await session.get(Resource, chunk.kb_id)
    citation = _chunk_citation(chunk, doc, kb_resource.name if kb_resource is not None else "")
    whole = await can_view_whole(session, ctx, chunk.kb_id)
    return await render_preview(session, storage, citation, whole_document=whole)


__all__ = [
    "KB_REQUIRED",
    "MODEL_REQUIRED",
    "debug_stream",
    "prepare_run",
    "preview_chunk",
]
