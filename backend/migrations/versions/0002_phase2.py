"""phase2：模型表与用量表（技术方案 6.3）

Revision ID: 0002_phase2
Revises: 0001_phase1
Create Date: 2026-10-24

本平台只做向前迁移，不实现 downgrade。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_phase2"
down_revision: str | None = "0001_phase1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "models",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="enabled", nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
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
        sa.Column("model_type", sa.Text(), nullable=False),
        sa.Column("litellm_model", sa.Text(), nullable=False),
        sa.Column("api_base", sa.Text(), nullable=True),
        sa.Column("api_key_cipher", sa.LargeBinary(), nullable=True),
        sa.Column("api_key_last4", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column("max_output", sa.Integer(), nullable=True),
        sa.Column("cap_vision", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cap_tools", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cap_thinking", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("embedding_max_input", sa.Integer(), nullable=True),
        sa.Column("embedding_batch_size", sa.Integer(), nullable=True),
        sa.Column(
            "default_params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "first_token_timeout_s",
            sa.Integer(),
            server_default=sa.text("30"),
            nullable=False,
        ),
        sa.Column(
            "total_timeout_s",
            sa.Integer(),
            server_default=sa.text("300"),
            nullable=False,
        ),
        sa.Column("max_retries", sa.Integer(), server_default=sa.text("2"), nullable=False),
        sa.Column("rate_qps", sa.Integer(), nullable=True),
        sa.Column("rate_concurrency", sa.Integer(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_message", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "embedding_dim IS NULL OR embedding_dim BETWEEN 1 AND 4000",
            name=op.f("ck_models_embedding_dim_range"),
        ),
        sa.CheckConstraint(
            "model_type IN ('chat', 'embedding', 'rerank')", name=op.f("ck_models_model_type")
        ),
        sa.CheckConstraint("status IN ('enabled', 'disabled')", name=op.f("ck_models_status")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_models_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_models")),
    )
    # 显示名称全平台唯一，软删除后可再用（需求 3.4）。
    op.create_index(
        "uq_models_name",
        "models",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "model_usage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("model_id", sa.BigInteger(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("first_token_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "source IN ('playground', 'app', 'app_debug', 'api', 'ingest', "
            "'model_test', 'kb_hit_test', 'scheme_trial')",
            name=op.f("ck_model_usage_source"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_usage")),
    )
    op.create_index("ix_model_usage_at", "model_usage", ["at"], unique=False)
    op.create_index("ix_model_usage_org_at", "model_usage", ["org_id", "at"], unique=False)
    op.create_index("ix_model_usage_model_at", "model_usage", ["model_id", "at"], unique=False)
    op.create_index(
        "ix_model_usage_source_at", "model_usage", ["source", "source_id", "at"], unique=False
    )


def downgrade() -> None:
    raise NotImplementedError("本平台只做向前迁移")
