"""对话日志：会话列表、会话回放与日志里的引用预览（应用技术方案 12.2）。

日志是只读接口，不写审计（应用技术方案 14）。权限要求应用的“编辑”（14），仅有
“查看”权限返回 403；应用删除后 `access.get_for` 抛 404，接口不再返回（12.2、5.4.2）。

会话列表按会话所属的应用过滤；时间筛选按 `chat_answers.created_at`（10）；点踩筛选
按 `answer_feedback.rating`。终端用户删除的会话照常显示并标注“用户已删除”（5.4.5）。

会话回放复用 `conversations.list_turns`（返回每一问的全部回答，含重新生成前的旧回答），
再单独读 `answer_traces` 补上处理过程（10）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import sqlalchemy as sa
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.tables import AppVersion
from app.chat import citations, conversations
from app.chat.schemas import (
    LogConversationItemOut,
    LogConversationOut,
    TraceOut,
    TraceStepOut,
)
from app.chat.tables import AnswerFeedback, AnswerTrace, ChatAnswer, ChatTurn, Conversation
from app.core.errors import NotFoundError
from app.core.schemas import OrgBrief, Page, Pagination, UserBrief
from app.core.storage import Storage
from app.iam.context import Ctx
from app.iam.tables import Org, User
from app.resources import access
from app.resources.tables import ResourceRef

APP_TYPE = "app"

# 时间筛选的天数（12.2、原型 `#v-applog`）；`all` 不筛。
RANGE_DAYS = {"7d": 7, "30d": 30}


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _time_clause(cutoff: datetime) -> sa.ColumnElement[bool]:
    """会话里至少有一条回答落在范围内（10：按 `chat_answers.created_at` 查）。"""
    return sa.exists(
        sa.select(ChatAnswer.id)
        .join(ChatTurn, ChatTurn.id == ChatAnswer.turn_id)
        .where(
            ChatTurn.conversation_id == Conversation.id,
            ChatAnswer.created_at >= cutoff,
        )
    )


def _feedback_clause(rating: str) -> sa.ColumnElement[bool]:
    """会话里有该评分的反馈（10：按 `answer_feedback` 查）。"""
    return sa.exists(
        sa.select(AnswerFeedback.id)
        .join(ChatAnswer, ChatAnswer.id == AnswerFeedback.answer_id)
        .join(ChatTurn, ChatTurn.id == ChatAnswer.turn_id)
        .where(
            ChatTurn.conversation_id == Conversation.id,
            AnswerFeedback.rating == rating,
        )
    )


# ---- 会话列表 ----


async def list_log_conversations(
    session: AsyncSession,
    ctx: Ctx,
    app_id: int,
    pagination: Pagination,
    *,
    time_range: Literal["7d", "30d", "all"] = "7d",
    feedback: Literal["all", "up", "down"] = "all",
    keyword: str | None = None,
) -> Page[LogConversationItemOut]:
    """按会话列出日志；要求应用“编辑”权限（14）。"""
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)

    conditions: list[sa.ColumnElement[bool]] = [Conversation.app_id == app_id]
    days = RANGE_DAYS.get(time_range)
    if days is not None:
        conditions.append(_time_clause(datetime.now(UTC) - timedelta(days=days)))
    if feedback in ("up", "down"):
        conditions.append(_feedback_clause(feedback))
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        conditions.append(
            sa.or_(
                Conversation.title.ilike(pattern, escape="\\"),
                User.name.ilike(pattern, escape="\\"),
                User.username.ilike(pattern, escape="\\"),
            )
        )

    total = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Conversation)
        .join(User, User.id == Conversation.user_id)
        .where(*conditions)
    )
    rows = (
        await session.execute(
            sa.select(Conversation, User, Org)
            .join(User, User.id == Conversation.user_id)
            .join(Org, Org.id == User.org_id)
            .where(*conditions)
            .order_by(Conversation.last_active_at.desc(), Conversation.id.desc())
            .limit(pagination.page_size)
            .offset((pagination.page - 1) * pagination.page_size)
        )
    ).all()

    items = await _list_items(session, rows)
    return Page(items=items, total=total or 0)


async def _list_items(session: AsyncSession, rows: list[sa.Row]) -> list[LogConversationItemOut]:
    conversation_ids = [row.Conversation.id for row in rows]
    if not conversation_ids:
        return []

    turn_counts = dict(
        (
            await session.execute(
                sa.select(ChatTurn.conversation_id, sa.func.count())
                .where(ChatTurn.conversation_id.in_(conversation_ids))
                .group_by(ChatTurn.conversation_id)
            )
        ).all()
    )

    feedback_rows = (
        await session.execute(
            sa.select(ChatTurn.conversation_id, AnswerFeedback.rating)
            .join(ChatAnswer, ChatAnswer.turn_id == ChatTurn.id)
            .join(AnswerFeedback, AnswerFeedback.answer_id == ChatAnswer.id)
            .where(ChatTurn.conversation_id.in_(conversation_ids))
        )
    ).all()
    has_feedback = {row.conversation_id for row in feedback_rows}
    has_down_feedback = {row.conversation_id for row in feedback_rows if row.rating == "down"}

    # 版本取会话里最后一条回答所用的版本；会话可能跨版本。
    latest_version: dict[int, int | None] = {}
    for conversation_id, version_id in (
        await session.execute(
            sa.select(ChatTurn.conversation_id, ChatAnswer.version_id)
            .join(ChatAnswer, ChatAnswer.turn_id == ChatTurn.id)
            .where(ChatTurn.conversation_id.in_(conversation_ids))
            .order_by(ChatTurn.conversation_id, ChatAnswer.created_at.desc(), ChatAnswer.id.desc())
        )
    ).all():
        latest_version.setdefault(conversation_id, version_id)

    version_ids = {value for value in latest_version.values() if value is not None}
    version_nos: dict[int, int] = {}
    if version_ids:
        version_nos = dict(
            (
                await session.execute(
                    sa.select(AppVersion.id, AppVersion.version_no).where(
                        AppVersion.id.in_(version_ids)
                    )
                )
            ).all()
        )

    return [
        LogConversationItemOut(
            conversation_id=conversation.id,
            app_id=conversation.app_id,
            user=UserBrief(id=user.id, username=user.username, name=user.name),
            org=OrgBrief(id=org.id, name=org.name),
            title=conversation.title,
            started_at=conversation.created_at,
            turn_count=turn_counts.get(conversation.id, 0),
            version_no=version_nos.get(latest_version.get(conversation.id) or 0),
            has_feedback=conversation.id in has_feedback,
            has_down_feedback=conversation.id in has_down_feedback,
            user_deleted=conversation.deleted_at is not None,
        )
        for conversation, user, org in rows
    ]


# ---- 会话回放 ----


async def _load_traces(session: AsyncSession, answer_ids: list[int]) -> dict[int, TraceOut]:
    """按回答 ID 读 `answer_traces` 并还原成 `TraceOut`（10）。"""
    if not answer_ids:
        return {}
    rows = (
        (await session.execute(sa.select(AnswerTrace).where(AnswerTrace.answer_id.in_(answer_ids))))
        .scalars()
        .all()
    )
    return {
        row.answer_id: TraceOut(
            answer_id=row.answer_id,
            app_id=row.app_id,
            created_at=row.created_at,
            steps=[TraceStepOut.model_validate(step) for step in (row.trace or [])],
        )
        for row in rows
    }


async def get_log_conversation(
    session: AsyncSession, ctx: Ctx, app_id: int, conversation_id: int
) -> LogConversationOut:
    """打开一个会话回放：每一问的全部回答与处理过程；要求应用“编辑”权限。"""
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.app_id != app_id:
        raise NotFoundError()
    user = await session.get(User, conversation.user_id)
    if user is None:  # pragma: no cover - 用户不做硬删除
        raise NotFoundError()
    org = await session.get(Org, user.org_id) if user.org_id is not None else None

    turns = await conversations.list_turns(session, conversation.id)
    traces = await _load_traces(session, [answer.id for turn in turns for answer in turn.answers])
    for turn in turns:
        for answer in turn.answers:
            answer.trace = traces.get(answer.id)

    return LogConversationOut(
        conversation_id=conversation.id,
        app_id=conversation.app_id,
        user=UserBrief(id=user.id, username=user.username, name=user.name),
        org=OrgBrief(id=org.id if org is not None else 0, name=org.name if org else ""),
        title=conversation.title,
        created_at=conversation.created_at,
        user_deleted=conversation.deleted_at is not None,
        turns=turns,
    )


# ---- 日志里的引用预览 ----


async def preview_log_citation(
    session: AsyncSession,
    storage: Storage,
    ctx: Ctx,
    app_id: int,
    answer_id: int,
    n: int,
) -> Response:
    """日志里点击分块查看位置（12.2、8.3 末条）。

    要求应用“编辑”权限，且只放行该应用引用的知识库中的文档；搭建者本人对知识库
    有查看权限时返回整份原文，否则只返回被引用部分。
    """
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    answer = await session.get(ChatAnswer, answer_id)
    if answer is None:
        raise NotFoundError()
    turn = await session.get(ChatTurn, answer.turn_id)
    if turn is None:
        raise NotFoundError()
    conversation = await session.get(Conversation, turn.conversation_id)
    if conversation is None or conversation.app_id != app_id:
        raise NotFoundError()

    citation = citations.get_citation(answer, n)
    await _ensure_kb_referenced(session, app_id, citation.kb_id)
    whole_document = await citations.can_view_whole(session, ctx, citation.kb_id)
    return await citations.render_preview(
        session,
        storage,
        citation,
        whole_document=whole_document,
    )


async def _ensure_kb_referenced(session: AsyncSession, app_id: int, kb_id: int) -> None:
    """分块所在的知识库必须被该应用引用（`resource_refs` 登记草稿 ∪ 当前发布版本）。"""
    referenced = await session.scalar(
        sa.select(ResourceRef.id).where(
            ResourceRef.ref_resource_id == app_id,
            ResourceRef.target_type == "resource",
            ResourceRef.target_id == kb_id,
        )
    )
    if referenced is None:
        raise NotFoundError()


__all__ = [
    "RANGE_DAYS",
    "get_log_conversation",
    "list_log_conversations",
    "preview_log_citation",
]
