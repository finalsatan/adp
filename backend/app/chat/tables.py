"""对话（M3）业务表（应用技术方案 10、9.2、12.2、12.3）。

会话、轮次、回答（含引用快照）、处理过程与反馈。应用本身是软删除的资源，这些行都保留
（应用技术方案 12.2）。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ANSWER_KINDS = ("generated", "qa_direct", "fallback", "chitchat")
ANSWER_STATUSES = ("generating", "done", "stopped", "failed")
FEEDBACK_RATINGS = ("up", "down")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Conversation(Base):
    """终端用户会话。删除为软删除：对话页不显示，日志照常显示并标注“用户已删除”。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("apps.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_active_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    deleted_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.Index("ix_conversations_app_user_last_active", "app_id", "user_id", "last_active_at"),
    )


class ChatTurn(Base):
    """一问。`current_answer_id` 指向最新的回答，重新生成后更新。"""

    __tablename__ = "chat_turns"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    question: Mapped[str] = mapped_column(sa.Text, nullable=False)
    rewritten_question: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # 与 chat_answers.turn_id 循环引用；use_alter 让 SQLAlchemy 用 ALTER TABLE 补外键。
    current_answer_id: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey(
            "chat_answers.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_chat_turns_current_answer_id_chat_answers",
        ),
        nullable=True,
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (sa.UniqueConstraint("conversation_id", "seq", name="uq_chat_turns_seq"),)


class ChatAnswer(Base):
    """一条回答。一问多答（重新生成），`citations` 存引用快照（应用技术方案 8.2）。"""

    __tablename__ = "chat_answers"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    turn_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("chat_turns.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    version_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("app_versions.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("''"))
    reasoning: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("''"))
    citations: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    # 实际引用过的编号（应用技术方案 10）。
    cited: Mapped[list] = mapped_column(
        ARRAY(sa.BigInteger), nullable=False, server_default=sa.text("'{}'::bigint[]")
    )
    suggestions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    first_token_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.CheckConstraint(_in_list("kind", ANSWER_KINDS), name="kind"),
        sa.CheckConstraint(_in_list("status", ANSWER_STATUSES), name="status"),
        sa.Index("ix_chat_answers_turn_id", "turn_id"),
        sa.Index("ix_chat_answers_created_at", "created_at"),
    )


class AnswerTrace(Base):
    """回答的处理过程，单独成表（体积大，会话列表与对话页都不读它）。"""

    __tablename__ = "answer_traces"

    answer_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("chat_answers.id", ondelete="CASCADE"), primary_key=True
    )
    app_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    trace: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )


class AnswerFeedback(Base):
    """用户反馈。(answer_id, user_id) 唯一，再次点击为更新（应用技术方案 12.3）。"""

    __tablename__ = "answer_feedback"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    answer_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("chat_answers.id", ondelete="CASCADE"), nullable=False
    )
    app_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    rating: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reasons: Mapped[list] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")
    )
    correction: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # 纠错加入问答库后生成的问答对（第六期），以及处理人与时间。
    handled_qa_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    handled_by: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    handled_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.CheckConstraint(_in_list("rating", FEEDBACK_RATINGS), name="rating"),
        sa.UniqueConstraint("answer_id", "user_id", name="uq_answer_feedback_answer_user"),
    )
