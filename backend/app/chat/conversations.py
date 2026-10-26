"""会话与轮次 / 回答的读写（应用技术方案 9.2、10）。

终端用户的会话列表、新建 / 重命名 / 软删除，以及一问多答的读取；提问与重新生成
落库也在这里。会话标题取第一个问题的前 20 个字（Q9，不调用模型）；软删除后对话页
不再显示，日志（P4-12）照常显示并标注“用户已删除”。

进程崩溃时回答会停在 `generating`；读取时把 `generating` 且超过 10 分钟没有更新的
回答按“已中断”（`stopped`）显示，不做定时任务修正（9.2）。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.tables import AppVersion
from app.chat.prompts import HistoryRecord, HistoryTurn, take_history
from app.chat.schemas import (
    AnswerKind,
    AnswerOut,
    AnswerStatus,
    CitationOut,
    ConversationOut,
    TurnOut,
)
from app.chat.tables import AnswerTrace, ChatAnswer, ChatTurn, Conversation
from app.core.errors import AppError, NotFoundError
from app.core.schemas import Page, Pagination
from app.gateway.errors import ModelCallError

if TYPE_CHECKING:  # 避免与 pipeline 的运行时依赖成环
    from app.chat.pipeline import AnswerRun

# 新建会话的默认标题；第一问后按 Q9 换成问题的前 20 个字。
DEFAULT_CONVERSATION_TITLE = "新会话"
TITLE_LENGTH = 20
# `generating` 超过这个时长没有更新，读取时按“已中断”显示（9.2）。
STALE_GENERATING_SECONDS = 600

# 实际引用过的角标：`[n]` 与全角 `【n】`（8.1）。
_CITED_RE = re.compile(r"[\[【](\d+)[\]】]")


def extract_cited(text: str) -> list[int]:
    """从全文提取实际出现过的编号，去重并升序（8.1）。"""
    numbers = {int(match) for match in _CITED_RE.findall(text or "")}
    return sorted(numbers)


def _error_code(error: Exception | None) -> str | None:
    if error is None:
        return None
    if isinstance(error, AppError):
        return error.code
    if isinstance(error, ModelCallError):
        return error.error_code
    return type(error).__name__


def _conversation_out(conversation: Conversation) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        app_id=conversation.app_id,
        title=conversation.title,
        last_active_at=conversation.last_active_at,
        created_at=conversation.created_at,
    )


def _is_stale(updated_at: datetime | None) -> bool:
    if updated_at is None:
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at < datetime.now(UTC) - timedelta(seconds=STALE_GENERATING_SECONDS)


def _answer_out(answer: ChatAnswer, version_nos: dict[int, int]) -> AnswerOut:
    status = answer.status
    if status == AnswerStatus.generating.value and _is_stale(answer.updated_at):
        status = AnswerStatus.stopped.value
    return AnswerOut(
        id=answer.id,
        turn_id=answer.turn_id,
        seq=answer.seq,
        version_no=version_nos.get(answer.version_id) if answer.version_id else None,
        kind=answer.kind,
        status=status,
        content=answer.content,
        reasoning=answer.reasoning,
        citations=[CitationOut.model_validate(item) for item in (answer.citations or [])],
        cited=list(answer.cited or []),
        suggestions=list(answer.suggestions or []),
        error_code=answer.error_code,
        error_message=None,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
        first_token_ms=answer.first_token_ms,
        total_ms=answer.total_ms,
        feedback=None,
        trace=None,
        created_at=answer.created_at,
        updated_at=answer.updated_at,
    )


# ---- 会话 ----


async def list_conversations(
    session: AsyncSession, app_id: int, user_id: int, pagination: Pagination
) -> Page[ConversationOut]:
    """本人未删除的会话，按最近活跃时间倒序。"""
    condition = sa.and_(
        Conversation.app_id == app_id,
        Conversation.user_id == user_id,
        Conversation.deleted_at.is_(None),
    )
    total = await session.scalar(
        sa.select(sa.func.count()).select_from(Conversation).where(condition)
    )
    rows = (
        (
            await session.execute(
                sa.select(Conversation)
                .where(condition)
                .order_by(Conversation.last_active_at.desc(), Conversation.id.desc())
                .limit(pagination.page_size)
                .offset((pagination.page - 1) * pagination.page_size)
            )
        )
        .scalars()
        .all()
    )
    return Page(items=[_conversation_out(row) for row in rows], total=total or 0)


async def create_conversation(
    session: AsyncSession, app_id: int, user_id: int, title: str | None
) -> ConversationOut:
    conversation = Conversation(
        app_id=app_id,
        user_id=user_id,
        title=title or DEFAULT_CONVERSATION_TITLE,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return _conversation_out(conversation)


async def get_owned_conversation(
    session: AsyncSession, conversation_id: int, user_id: int
) -> Conversation:
    """取本人的未删除会话；不存在、已删除或不属于本人一律 404。"""
    conversation = await session.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.deleted_at is not None
        or conversation.user_id != user_id
    ):
        raise NotFoundError()
    return conversation


async def rename_conversation(
    session: AsyncSession, conversation: Conversation, title: str
) -> ConversationOut:
    conversation.title = title
    await session.commit()
    await session.refresh(conversation)
    return _conversation_out(conversation)


async def soft_delete_conversation(session: AsyncSession, conversation: Conversation) -> None:
    conversation.deleted_at = datetime.now(UTC)
    await session.commit()


# ---- 轮次与回答 ----


async def list_turns(session: AsyncSession, conversation_id: int) -> list[TurnOut]:
    """按顺序返回每一问和它的全部回答（重新生成前的旧回答也在）。"""
    turns = (
        (
            await session.execute(
                sa.select(ChatTurn)
                .where(ChatTurn.conversation_id == conversation_id)
                .order_by(ChatTurn.seq)
            )
        )
        .scalars()
        .all()
    )
    if not turns:
        return []
    answers = (
        (
            await session.execute(
                sa.select(ChatAnswer)
                .where(ChatAnswer.turn_id.in_([turn.id for turn in turns]))
                .order_by(ChatAnswer.turn_id, ChatAnswer.seq)
            )
        )
        .scalars()
        .all()
    )
    version_ids = {answer.version_id for answer in answers if answer.version_id is not None}
    version_nos: dict[int, int] = {}
    if version_ids:
        rows = (
            await session.execute(
                sa.select(AppVersion.id, AppVersion.version_no).where(
                    AppVersion.id.in_(version_ids)
                )
            )
        ).all()
        version_nos = {row.id: row.version_no for row in rows}
    by_turn: dict[int, list[ChatAnswer]] = {}
    for answer in answers:
        by_turn.setdefault(answer.turn_id, []).append(answer)
    return [
        TurnOut(
            id=turn.id,
            seq=turn.seq,
            question=turn.question,
            rewritten_question=turn.rewritten_question,
            current_answer_id=turn.current_answer_id or 0,
            created_at=turn.created_at,
            answers=[_answer_out(answer, version_nos) for answer in by_turn.get(turn.id, [])],
        )
        for turn in turns
    ]


async def create_turn(
    session: AsyncSession,
    conversation: Conversation,
    *,
    question: str,
    version_id: int | None,
) -> tuple[ChatTurn, ChatAnswer]:
    """插入一问和状态为 `generating` 的回答，刷新页面后即可看到（9.2）。"""
    next_seq = (
        await session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(ChatTurn.seq), 0) + 1).where(
                ChatTurn.conversation_id == conversation.id
            )
        )
    ) or 1
    turn = ChatTurn(conversation_id=conversation.id, seq=next_seq, question=question)
    session.add(turn)
    await session.flush()
    answer = ChatAnswer(
        turn_id=turn.id,
        seq=1,
        version_id=version_id,
        kind=AnswerKind.generated.value,
        status=AnswerStatus.generating.value,
    )
    session.add(answer)
    await session.flush()
    turn.current_answer_id = answer.id
    if next_seq == 1 and conversation.title == DEFAULT_CONVERSATION_TITLE:
        conversation.title = question[:TITLE_LENGTH]
    conversation.last_active_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(turn)
    await session.refresh(answer)
    return turn, answer


async def create_regeneration(
    session: AsyncSession, turn: ChatTurn, *, version_id: int | None
) -> ChatAnswer:
    """对同一问再生成一条回答（`seq` 加 1），并更新 `current_answer_id`（9.2）。"""
    next_seq = (
        await session.scalar(
            sa.select(sa.func.coalesce(sa.func.max(ChatAnswer.seq), 0) + 1).where(
                ChatAnswer.turn_id == turn.id
            )
        )
    ) or 1
    answer = ChatAnswer(
        turn_id=turn.id,
        seq=next_seq,
        version_id=version_id,
        kind=AnswerKind.generated.value,
        status=AnswerStatus.generating.value,
    )
    session.add(answer)
    await session.flush()
    turn.current_answer_id = answer.id
    conversation = await session.get(Conversation, turn.conversation_id)
    if conversation is not None:
        conversation.last_active_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(answer)
    return answer


async def load_history(
    session: AsyncSession,
    conversation_id: int,
    *,
    before_seq: int | None,
    memory_turns: int | None,
) -> list[HistoryTurn]:
    """取这一问之前各轮的问题和当前回答文字，按 `memory_turns` 截取（7.3、9.2）。"""
    query = (
        sa.select(ChatTurn.question, ChatAnswer.status, ChatAnswer.content)
        .join(ChatAnswer, ChatAnswer.id == ChatTurn.current_answer_id)
        .where(ChatTurn.conversation_id == conversation_id)
        .order_by(ChatTurn.seq)
    )
    if before_seq is not None:
        query = query.where(ChatTurn.seq < before_seq)
    rows = (await session.execute(query)).all()
    records = [
        HistoryRecord(question=row.question, status=row.status, content=row.content) for row in rows
    ]
    return take_history(records, memory_turns=memory_turns)


# ---- 落库 ----


async def save_answer_result(run: AnswerRun) -> None:
    """把一次生成的最终结果写进 `chat_answers` 与 `answer_traces`。

    调用方用**独立的数据库会话**并 `asyncio.shield` 保护，取消（客户端断开）时也能
    把已输出的部分写完（9.2）。`status` 由调用方按成功 / 失败 / 已中断设置。
    """
    async with run.sessionmaker() as session:
        answer = await session.get(ChatAnswer, run.answer_id)
        if answer is None:  # pragma: no cover - 正常流程不会缺
            return
        answer.content = run.content or ""
        answer.reasoning = run.reasoning or ""
        answer.citations = [citation.model_dump(mode="json") for citation in run.citations]
        answer.cited = extract_cited(run.content)
        answer.kind = run.kind.value if hasattr(run.kind, "value") else str(run.kind)
        answer.status = run.status.value if hasattr(run.status, "value") else str(run.status)
        answer.error_code = _error_code(run.error)
        answer.input_tokens = run.input_tokens
        answer.output_tokens = run.output_tokens
        answer.first_token_ms = run.first_token_ms
        answer.total_ms = run.total_ms
        answer.updated_at = sa.func.now()
        if run.turn_id is not None:
            turn = await session.get(ChatTurn, run.turn_id)
            if turn is not None and run.rewritten_question is not None:
                turn.rewritten_question = run.rewritten_question
        if run.trace is not None:
            trace = [step.model_dump(mode="json") for step in run.trace.build().steps]
            stored = await session.get(AnswerTrace, run.answer_id)
            if stored is None:
                session.add(AnswerTrace(answer_id=run.answer_id, app_id=run.app_id, trace=trace))
            else:
                stored.trace = trace
        await session.commit()
