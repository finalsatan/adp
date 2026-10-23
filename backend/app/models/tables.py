"""模型表。模型单独成表，不设所属组织和可见范围，全平台可用（需求 3.3）。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

MODEL_TYPES = ("chat", "embedding", "rerank")


class Model(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="enabled")
    created_by: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    model_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    litellm_model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    api_base: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # 密钥密文（AES-256-GCM，格式见 app/core/crypto.py）与末 4 位，明文不落库。
    api_key_cipher: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    api_key_last4: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    context_length: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    max_output: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    cap_vision: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    cap_tools: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    cap_thinking: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    embedding_dim: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    embedding_max_input: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    embedding_batch_size: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    default_params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    first_token_timeout_s: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("30")
    )
    total_timeout_s: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("300")
    )
    max_retries: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("2")
    )
    # 为空表示不限流。
    rate_qps: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    rate_concurrency: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    last_test_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_test_ok: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    deleted_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.CheckConstraint("status IN ('enabled', 'disabled')", name="status"),
        sa.CheckConstraint("model_type IN ('chat', 'embedding', 'rerank')", name="model_type"),
        sa.CheckConstraint(
            "embedding_dim IS NULL OR embedding_dim BETWEEN 1 AND 4000",
            name="embedding_dim_range",
        ),
        # 显示名称全平台唯一，软删除后可再用（需求 3.4）。
        sa.Index(
            "uq_models_name",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
