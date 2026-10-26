"""phase4：应用与对话业务表（应用技术方案 10）

Revision ID: 0005_phase4
Revises: 0004_builtin_schemes
Create Date: 2026-10-26

本平台只做向前迁移，不实现 downgrade。
只建表、索引、约束，不插数据。两组循环外键（apps.published_version_id ↔
app_versions.app_id、chat_turns.current_answer_id ↔ chat_answers.turn_id）先建两张表，
再用 ALTER TABLE 补其中一条，保证空库和已有数据的库都能跑。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_phase4"
down_revision: str | None = "0004_builtin_schemes"
branch_labels: str | None = None
depends_on: str | None = None

APP_TYPES = ("kb_qa",)
USAGE_SCOPES = ("platform", "orgs", "users")
USAGE_TARGET_TYPES = ("org", "user")
ANSWER_KINDS = ("generated", "qa_direct", "fallback", "chitchat")
ANSWER_STATUSES = ("generating", "done", "stopped", "failed")
FEEDBACK_RATINGS = ("up", "down")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("app_type", sa.Text(), nullable=False),
        sa.Column(
            "draft_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("draft_revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("draft_updated_by", sa.BigInteger(), nullable=True),
        sa.Column("draft_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_version_id", sa.BigInteger(), nullable=True),
        sa.Column("usage_scope", sa.Text(), nullable=False),
        sa.CheckConstraint(_in_list("app_type", APP_TYPES), name=op.f("ck_apps_app_type")),
        sa.CheckConstraint(_in_list("usage_scope", USAGE_SCOPES), name=op.f("ck_apps_usage_scope")),
        sa.ForeignKeyConstraint(
            ["id"],
            ["resources.id"],
            name=op.f("fk_apps_id_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_apps")),
    )

    op.create_table(
        "app_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("app_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("published_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["apps.id"],
            name=op.f("fk_app_versions_app_id_apps"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_versions")),
        sa.UniqueConstraint("app_id", "version_no", name=op.f("uq_app_versions_app_version_no")),
    )

    # apps ↔ app_versions 循环外键：后补 apps 这一侧。
    op.create_foreign_key(
        "fk_apps_published_version_id_app_versions",
        "apps",
        "app_versions",
        ["published_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "app_usage_targets",
        sa.Column("app_id", sa.BigInteger(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            _in_list("target_type", USAGE_TARGET_TYPES),
            name=op.f("ck_app_usage_targets_target_type"),
        ),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["apps.id"],
            name=op.f("fk_app_usage_targets_app_id_apps"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "app_id", "target_type", "target_id", name=op.f("pk_app_usage_targets")
        ),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("app_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], name=op.f("fk_conversations_app_id_apps")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(
        "ix_conversations_app_user_last_active",
        "conversations",
        ["app_id", "user_id", "last_active_at"],
        unique=False,
    )

    op.create_table(
        "chat_turns",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("rewritten_question", sa.Text(), nullable=True),
        sa.Column("current_answer_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_chat_turns_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_turns")),
        sa.UniqueConstraint("conversation_id", "seq", name=op.f("uq_chat_turns_seq")),
    )

    op.create_table(
        "chat_answers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("turn_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("reasoning", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "cited",
            postgresql.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'::bigint[]"),
            nullable=False,
        ),
        sa.Column(
            "suggestions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("first_token_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in_list("kind", ANSWER_KINDS), name=op.f("ck_chat_answers_kind")),
        sa.CheckConstraint(
            _in_list("status", ANSWER_STATUSES), name=op.f("ck_chat_answers_status")
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turns.id"],
            name=op.f("fk_chat_answers_turn_id_chat_turns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["app_versions.id"],
            name=op.f("fk_chat_answers_version_id_app_versions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_answers")),
    )
    op.create_index("ix_chat_answers_turn_id", "chat_answers", ["turn_id"], unique=False)
    op.create_index("ix_chat_answers_created_at", "chat_answers", ["created_at"], unique=False)

    # chat_turns ↔ chat_answers 循环外键：后补 chat_turns 这一侧。
    op.create_foreign_key(
        "fk_chat_turns_current_answer_id_chat_answers",
        "chat_turns",
        "chat_answers",
        ["current_answer_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "answer_traces",
        sa.Column("answer_id", sa.BigInteger(), nullable=False),
        sa.Column("app_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "trace",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["answer_id"],
            ["chat_answers.id"],
            name=op.f("fk_answer_traces_answer_id_chat_answers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("answer_id", name=op.f("pk_answer_traces")),
    )

    op.create_table(
        "answer_feedback",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("answer_id", sa.BigInteger(), nullable=False),
        sa.Column("app_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column(
            "reasons",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("correction", sa.Text(), nullable=True),
        sa.Column("handled_qa_id", sa.BigInteger(), nullable=True),
        sa.Column("handled_by", sa.BigInteger(), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("rating", FEEDBACK_RATINGS), name=op.f("ck_answer_feedback_rating")
        ),
        sa.ForeignKeyConstraint(
            ["answer_id"],
            ["chat_answers.id"],
            name=op.f("fk_answer_feedback_answer_id_chat_answers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_feedback")),
        sa.UniqueConstraint("answer_id", "user_id", name=op.f("uq_answer_feedback_answer_user")),
    )


def downgrade() -> None:
    raise NotImplementedError("本平台只做向前迁移")
