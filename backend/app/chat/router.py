"""对话、调试、对话日志与用户反馈接口（P4-01 契约）。

路由只做参数接收、权限依赖、调用 service；本卡是占位（返回 501），实现见
P4-09（会话与流式回答）、P4-10（引用预览）、P4-11（调试）、P4-12（日志）、
P4-13（反馈）。

终端用户接口不要求控制台权限码，只要求登录；使用范围判断在实现卡（应用技术方案 11）。
"""

from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps import usage
from app.apps.tables import App, AppVersion
from app.apps.validation import parse_config
from app.chat import citations, conversations, logs, streaming
from app.chat.schemas import (
    AppChatInfoOut,
    AskIn,
    CitationPreviewOut,
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    DebugChatIn,
    FeedbackIn,
    FeedbackOut,
    FeedbackPage,
    FeedbackRating,
    LogConversationItemOut,
    LogConversationOut,
    TurnOut,
)
from app.chat.tables import ChatTurn
from app.core.db import get_session
from app.core.errors import AppNotPublishedError, NotFoundError, NotLastTurnError
from app.core.redis import get_redis
from app.core.schemas import Page, Pagination
from app.iam.context import current_ctx
from app.iam.deps import CurrentCtx

router = APIRouter(prefix="/api/v1")

AUTH = [Depends(current_ctx)]

SSE_RESPONSES = {
    200: {
        "description": "SSE 事件流",
        "content": {"text/event-stream": {"schema": {"$ref": "#/components/schemas/ChatSseEvent"}}},
    }
}
# 引用预览按文件类型区分：PDF、PPT 返回只含被引用页的 PDF，Word 及其余类型返回 JSON。
PREVIEW_RESPONSES = {
    200: {
        "description": "引用预览：PDF、PPT 为文件流，Word 及其余类型为 JSON",
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/CitationPreviewOut"}},
            "application/pdf": {"schema": {"type": "string", "format": "binary"}},
        },
    }
}
FILE_RESPONSES = {
    200: {
        "description": "原文件流（支持 HTTP Range）",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


# ---- 调试 ----


@router.post(
    "/apps/{id:int}/debug/chat",
    response_class=StreamingResponse,
    dependencies=AUTH,
    tags=["调试"],
    summary="调试对话（SSE）",
    responses=SSE_RESPONSES,
)
async def debug_chat(
    id: int,
    body: DebugChatIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    raise NotImplementedError


@router.get(
    "/apps/{id:int}/debug/chunks/{chunk:int}/preview",
    response_model=CitationPreviewOut,
    response_class=Response,
    dependencies=AUTH,
    tags=["调试"],
    summary="调试分块位置预览",
    responses=PREVIEW_RESPONSES,
)
async def debug_chunk_preview(
    id: int,
    chunk: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    raise NotImplementedError


# ---- 对话日志 ----


@router.get(
    "/apps/{id:int}/logs/conversations",
    response_model=Page[LogConversationItemOut],
    dependencies=AUTH,
    tags=["对话日志"],
    summary="对话日志会话列表",
)
async def list_log_conversations(
    id: int,
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    range: Literal["7d", "30d", "all"] = "7d",
    feedback: Literal["all", "up", "down"] = "all",
    keyword: str | None = None,
) -> Page[LogConversationItemOut]:
    return await logs.list_log_conversations(
        session, ctx, id, pagination, time_range=range, feedback=feedback, keyword=keyword
    )


@router.get(
    "/apps/{id:int}/logs/conversations/{conv:int}",
    response_model=LogConversationOut,
    dependencies=AUTH,
    tags=["对话日志"],
    summary="对话日志会话详情",
)
async def get_log_conversation(
    id: int,
    conv: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogConversationOut:
    return await logs.get_log_conversation(session, ctx, id, conv)


@router.get(
    "/apps/{id:int}/logs/answers/{answer:int}/citations/{n:int}/preview",
    response_model=CitationPreviewOut,
    response_class=Response,
    dependencies=AUTH,
    tags=["对话日志"],
    summary="日志里的引用预览",
    responses=PREVIEW_RESPONSES,
)
async def log_citation_preview(
    id: int,
    answer: int,
    n: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    return await logs.preview_log_citation(session, request.app.state.storage, ctx, id, answer, n)


# ---- 用户反馈 ----


@router.get(
    "/apps/{id:int}/feedback",
    response_model=FeedbackPage,
    dependencies=AUTH,
    tags=["用户反馈"],
    summary="用户反馈列表",
)
async def list_app_feedback(
    id: int,
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    rating: FeedbackRating | None = None,
    has_correction: bool | None = None,
    handled: bool | None = None,
    range: Literal["7d", "30d", "all"] = "7d",
) -> FeedbackPage:
    raise NotImplementedError


# ---- 终端用户 ----


@router.get(
    "/chat/apps/{id:int}",
    response_model=AppChatInfoOut,
    dependencies=AUTH,
    tags=["对话"],
    summary="对话页应用信息",
)
async def get_chat_app(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppChatInfoOut:
    app, resource = await usage.get_chat_app(session, ctx, id)
    version = await session.get(AppVersion, app.published_version_id)
    if version is None:
        raise AppNotPublishedError()
    reply = parse_config(app.app_type, version.config).reply
    return AppChatInfoOut(
        id=resource.id,
        name=resource.name,
        description=resource.description,
        opening=reply.opening,
        opening_questions=reply.opening_questions,
        suggestions=reply.suggestions,
        allow_download=reply.allow_download,
    )


@router.get(
    "/chat/apps/{id:int}/conversations",
    response_model=Page[ConversationOut],
    dependencies=AUTH,
    tags=["对话"],
    summary="会话列表",
)
async def list_conversations(
    id: int,
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Page[ConversationOut]:
    app, _ = await usage.get_chat_app(session, ctx, id)
    return await conversations.list_conversations(session, app.id, ctx.user_id, pagination)


@router.post(
    "/chat/apps/{id:int}/conversations",
    response_model=ConversationOut,
    status_code=201,
    dependencies=AUTH,
    tags=["对话"],
    summary="新建会话",
)
async def create_conversation(
    id: int,
    body: ConversationCreate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationOut:
    app, _ = await usage.get_chat_app(session, ctx, id)
    return await conversations.create_conversation(session, app.id, ctx.user_id, body.title)


@router.patch(
    "/conversations/{id:int}",
    response_model=ConversationOut,
    dependencies=AUTH,
    tags=["对话"],
    summary="重命名会话",
)
async def update_conversation(
    id: int,
    body: ConversationUpdate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationOut:
    conversation = await conversations.get_owned_conversation(session, id, ctx.user_id)
    await usage.get_chat_app(session, ctx, conversation.app_id)
    return await conversations.rename_conversation(session, conversation, body.title)


@router.delete(
    "/conversations/{id:int}",
    status_code=204,
    dependencies=AUTH,
    tags=["对话"],
    summary="删除会话",
)
async def delete_conversation(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    conversation = await conversations.get_owned_conversation(session, id, ctx.user_id)
    await usage.get_chat_app(session, ctx, conversation.app_id)
    await conversations.soft_delete_conversation(session, conversation)


@router.get(
    "/conversations/{id:int}/turns",
    response_model=list[TurnOut],
    dependencies=AUTH,
    tags=["对话"],
    summary="会话的轮次",
)
async def list_conversation_turns(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TurnOut]:
    conversation = await conversations.get_owned_conversation(session, id, ctx.user_id)
    await usage.get_chat_app(session, ctx, conversation.app_id)
    return await conversations.list_turns(session, conversation.id)


async def _published_version(session: AsyncSession, app: App) -> AppVersion:
    version = await session.get(AppVersion, app.published_version_id)
    if version is None:
        raise AppNotPublishedError()
    return version


@router.post(
    "/conversations/{id:int}/turns",
    response_class=StreamingResponse,
    dependencies=AUTH,
    tags=["对话"],
    summary="提问（SSE）",
    responses=SSE_RESPONSES,
)
async def ask_turn(
    id: int,
    body: AskIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> StreamingResponse:
    conversation = await conversations.get_owned_conversation(session, id, ctx.user_id)
    app, _ = await usage.get_chat_app(session, ctx, conversation.app_id)
    version = await _published_version(session, app)
    config = parse_config(app.app_type, version.config)
    lock = await streaming.acquire_conversation_lock(redis, conversation.id)
    try:
        history = await conversations.load_history(
            session,
            conversation.id,
            before_seq=None,
            memory_turns=config.memory_turns,
        )
        turn, answer = await conversations.create_turn(
            session, conversation, question=body.question, version_id=version.id
        )
    except BaseException:
        await lock.release()
        raise
    return streaming.answer_stream(
        request,
        sessionmaker=request.app.state.sessionmaker,
        redis=redis,
        lock=lock,
        app=app,
        version=version,
        config=config,
        conversation_id=conversation.id,
        turn=turn,
        answer=answer,
        question=body.question,
        history=history,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
    )


@router.post(
    "/turns/{id:int}:regenerate",
    response_class=StreamingResponse,
    dependencies=AUTH,
    tags=["对话"],
    summary="重新生成（SSE）",
    responses=SSE_RESPONSES,
)
async def regenerate_turn(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> StreamingResponse:
    turn = await session.get(ChatTurn, id)
    if turn is None:
        raise NotFoundError()
    conversation = await conversations.get_owned_conversation(
        session, turn.conversation_id, ctx.user_id
    )
    last_seq = await session.scalar(
        sa.select(sa.func.max(ChatTurn.seq)).where(ChatTurn.conversation_id == conversation.id)
    )
    if last_seq != turn.seq:
        raise NotLastTurnError()
    app, _ = await usage.get_chat_app(session, ctx, conversation.app_id)
    version = await _published_version(session, app)
    config = parse_config(app.app_type, version.config)
    lock = await streaming.acquire_conversation_lock(redis, conversation.id)
    try:
        history = await conversations.load_history(
            session,
            conversation.id,
            before_seq=turn.seq,
            memory_turns=config.memory_turns,
        )
        answer = await conversations.create_regeneration(session, turn, version_id=version.id)
    except BaseException:
        await lock.release()
        raise
    return streaming.answer_stream(
        request,
        sessionmaker=request.app.state.sessionmaker,
        redis=redis,
        lock=lock,
        app=app,
        version=version,
        config=config,
        conversation_id=conversation.id,
        turn=turn,
        answer=answer,
        question=turn.question,
        history=history,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
    )


@router.put(
    "/answers/{id:int}/feedback",
    response_model=FeedbackOut,
    dependencies=AUTH,
    tags=["用户反馈"],
    summary="点赞 / 点踩",
)
async def put_answer_feedback(
    id: int,
    body: FeedbackIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FeedbackOut:
    raise NotImplementedError


@router.delete(
    "/answers/{id:int}/feedback",
    status_code=204,
    dependencies=AUTH,
    tags=["用户反馈"],
    summary="取消点赞 / 点踩",
)
async def delete_answer_feedback(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    raise NotImplementedError


@router.get(
    "/answers/{id:int}/citations/{n:int}/preview",
    response_model=CitationPreviewOut,
    response_class=Response,
    dependencies=AUTH,
    tags=["对话"],
    summary="引用预览",
    responses=PREVIEW_RESPONSES,
)
async def answer_citation_preview(
    id: int,
    n: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    answer, _, app = await citations.get_owned_answer(session, ctx, id)
    citation = citations.get_citation(answer, n)
    allow_download = await citations.current_allow_download(session, app)
    return await citations.render_preview(
        session,
        request.app.state.storage,
        citation,
        download_allowed=allow_download,
    )


@router.get(
    "/answers/{id:int}/citations/{n:int}/file",
    response_class=Response,
    dependencies=AUTH,
    tags=["对话"],
    summary="下载原文件",
    responses=FILE_RESPONSES,
)
async def answer_citation_file(
    id: int,
    n: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    download: bool = False,
) -> Response:
    answer, _, app = await citations.get_owned_answer(session, ctx, id)
    citation = citations.get_citation(answer, n)
    allow_download = await citations.current_allow_download(session, app)
    return await citations.stream_citation_file(
        session,
        request.app.state.storage,
        citation,
        download_allowed=allow_download,
        download=download,
        range_header=request.headers.get("range"),
    )
