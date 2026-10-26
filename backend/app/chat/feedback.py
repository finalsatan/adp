"""用户反馈：终端用户点赞 / 点踩 / 纠错，以及搭建者的反馈列表（应用技术方案 12.3）。

终端用户只能对自己会话中的回答反馈，且本人仍在该应用的使用范围内（12.3、11）：
复用 `citations.get_owned_answer`——会话不属于本人返回 403，回答 / 会话不存在返回
404，再走使用范围判断（未发布 / 停用 / 移出范围返回对应 403）。`(answer_id, user_id)`
唯一，再次点击为更新（不新增行），取消点赞 / 点踩即删除这一行（10）。

反馈列表要求应用“编辑”权限（14），仅有“查看”权限返回 403；列表按反馈时间倒序，
可按点赞、点踩、有无纠错、是否已处理与时间范围筛选；顶部统计按应用与时间范围汇总，
不受点赞 / 纠错 / 处理状态筛选影响（原型 `#v-appfb` 的统计是全局的）。

纠错加入问答库属第六期（`POST /feedback/{id}:add-to-qa`），本卡不做：`handled_qa_id`
恒为空，“是否已处理”只筛 `handled_qa_id`。反馈是终端用户行为，不写审计（14）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import citations
from app.chat.logs import APP_TYPE, RANGE_DAYS
from app.chat.schemas import (
    FeedbackIn,
    FeedbackListItemOut,
    FeedbackOut,
    FeedbackPage,
    FeedbackRating,
    FeedbackReason,
    FeedbackSummary,
)
from app.chat.tables import AnswerFeedback, ChatAnswer, ChatTurn
from app.core.schemas import OrgBrief, Pagination, UserBrief
from app.iam.context import Ctx
from app.iam.tables import Org, User
from app.resources import access

# 列表里回答摘要的最大长度（卡片未指定，纯展示用）。
ANSWER_EXCERPT_LENGTH = 200


def _feedback_out(feedback: AnswerFeedback) -> FeedbackOut:
    return FeedbackOut(
        id=feedback.id,
        answer_id=feedback.answer_id,
        rating=feedback.rating,
        reasons=[FeedbackReason(reason) for reason in (feedback.reasons or [])],
        correction=feedback.correction,
        created_at=feedback.created_at,
        updated_at=feedback.updated_at,
    )


def _has_correction_clause() -> sa.ColumnElement[bool]:
    """“有纠错”按纠错内容非空判断（`null` 与空串都算没有）。"""
    return sa.and_(
        AnswerFeedback.correction.isnot(None),
        AnswerFeedback.correction != "",
    )


# ---- 终端用户 ----


async def upsert_answer_feedback(
    session: AsyncSession, ctx: Ctx, answer_id: int, body: FeedbackIn
) -> FeedbackOut:
    """点赞 / 点踩（含原因与纠错）；同一用户对同一回答再次提交为更新（12.3）。"""
    answer, _, app = await citations.get_owned_answer(session, ctx, answer_id)

    # 原因与纠错只对点踩有意义；改点赞时清掉，避免残留（契约里二者“点踩时才有意义”）。
    if body.rating == FeedbackRating.down:
        reasons = [reason.value for reason in body.reasons]
        correction = body.correction
    else:
        reasons = []
        correction = None

    feedback = await session.scalar(
        sa.select(AnswerFeedback).where(
            AnswerFeedback.answer_id == answer.id,
            AnswerFeedback.user_id == ctx.user_id,
        )
    )
    if feedback is None:
        feedback = AnswerFeedback(
            answer_id=answer.id,
            app_id=app.id,
            user_id=ctx.user_id,
            rating=body.rating.value,
            reasons=reasons,
            correction=correction,
        )
        session.add(feedback)
    else:
        feedback.rating = body.rating.value
        feedback.reasons = reasons
        feedback.correction = correction
        feedback.updated_at = sa.func.now()
    await session.commit()
    await session.refresh(feedback)
    return _feedback_out(feedback)


async def delete_answer_feedback(session: AsyncSession, ctx: Ctx, answer_id: int) -> None:
    """取消点赞 / 点踩：删除这一行；没有反馈时也返回成功（幂等，12.3）。"""
    await citations.get_owned_answer(session, ctx, answer_id)
    await session.execute(
        sa.delete(AnswerFeedback).where(
            AnswerFeedback.answer_id == answer_id,
            AnswerFeedback.user_id == ctx.user_id,
        )
    )
    await session.commit()


# ---- 搭建者反馈列表 ----


async def list_app_feedback(
    session: AsyncSession,
    ctx: Ctx,
    app_id: int,
    pagination: Pagination,
    *,
    rating: FeedbackRating | None = None,
    has_correction: bool | None = None,
    handled: bool | None = None,
    time_range: Literal["7d", "30d", "all"] = "7d",
) -> FeedbackPage:
    """按时间倒序返回应用的反馈列表与顶部统计；要求应用“编辑”权限（12.3、14）。"""
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)

    conditions: list[sa.ColumnElement[bool]] = [AnswerFeedback.app_id == app_id]
    days = RANGE_DAYS.get(time_range)
    if days is not None:
        conditions.append(AnswerFeedback.created_at >= datetime.now(UTC) - timedelta(days=days))
    if rating is not None:
        conditions.append(AnswerFeedback.rating == rating.value)
    if has_correction is not None:
        clause = _has_correction_clause()
        conditions.append(clause if has_correction else sa.not_(clause))
    if handled is not None:
        conditions.append(
            AnswerFeedback.handled_qa_id.isnot(None)
            if handled
            else AnswerFeedback.handled_qa_id.is_(None)
        )

    total = await session.scalar(
        sa.select(sa.func.count()).select_from(AnswerFeedback).where(*conditions)
    )
    rows = (
        await session.execute(
            sa.select(AnswerFeedback, ChatAnswer, ChatTurn, User, Org)
            .join(ChatAnswer, ChatAnswer.id == AnswerFeedback.answer_id)
            .join(ChatTurn, ChatTurn.id == ChatAnswer.turn_id)
            .join(User, User.id == AnswerFeedback.user_id)
            .join(Org, Org.id == User.org_id)
            .where(*conditions)
            .order_by(AnswerFeedback.created_at.desc(), AnswerFeedback.id.desc())
            .limit(pagination.page_size)
            .offset((pagination.page - 1) * pagination.page_size)
        )
    ).all()

    items = [
        FeedbackListItemOut(
            id=feedback.id,
            answer_id=feedback.answer_id,
            conversation_id=turn.conversation_id,
            user=UserBrief(id=user.id, username=user.username, name=user.name),
            org=OrgBrief(id=org.id, name=org.name),
            question=turn.question,
            answer_excerpt=answer.content[:ANSWER_EXCERPT_LENGTH],
            rating=feedback.rating,
            reasons=[FeedbackReason(reason) for reason in (feedback.reasons or [])],
            correction=feedback.correction,
            handled=feedback.handled_qa_id is not None,
            created_at=feedback.created_at,
        )
        for feedback, answer, turn, user, org in rows
    ]

    # 顶部统计只按应用与时间范围汇总，不随点赞 / 纠错 / 处理状态筛选变化。
    summary_conditions: list[sa.ColumnElement[bool]] = [AnswerFeedback.app_id == app_id]
    if days is not None:
        summary_conditions.append(
            AnswerFeedback.created_at >= datetime.now(UTC) - timedelta(days=days)
        )
    up, down, down_with_correction = (
        await session.execute(
            sa.select(
                sa.func.count().filter(AnswerFeedback.rating == "up"),
                sa.func.count().filter(AnswerFeedback.rating == "down"),
                sa.func.count().filter(
                    sa.and_(AnswerFeedback.rating == "down", _has_correction_clause())
                ),
            ).where(*summary_conditions)
        )
    ).one()

    return FeedbackPage(
        items=items,
        total=total or 0,
        summary=FeedbackSummary(
            up=up or 0,
            down=down or 0,
            down_with_correction=down_with_correction or 0,
        ),
    )


__all__ = [
    "ANSWER_EXCERPT_LENGTH",
    "delete_answer_feedback",
    "list_app_feedback",
    "upsert_answer_feedback",
]
