"""模型调用用量表。每次调用一条，供用量统计（技术方案 6.3、9.7）。

日志表，不加外键：模型、用户、组织都可能被软删除或调走，记录里是当时的快照。
"""

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

USAGE_SOURCES = (
    "playground",
    "app",
    "app_debug",
    "api",
    "ingest",
    "model_test",
    "kb_hit_test",
    "scheme_trial",
)


class ModelUsage(Base):
    __tablename__ = "model_usage"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    # 保存前的连通测试没有模型 id。
    model_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    # 名称快照：模型改名或删除后，用量记录仍显示当时的名称。
    model_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    user_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    # 发起人当时所属组织（系统管理员为空）。
    org_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    first_token_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "source IN ('playground', 'app', 'app_debug', 'api', 'ingest', "
            "'model_test', 'kb_hit_test', 'scheme_trial')",
            name="source",
        ),
        sa.Index("ix_model_usage_at", "at"),
        sa.Index("ix_model_usage_org_at", "org_id", "at"),
        sa.Index("ix_model_usage_model_at", "model_id", "at"),
        # 按应用汇总（source='app' / 'app_debug' + source_id）用。
        sa.Index("ix_model_usage_source_at", "source", "source_id", "at"),
    )
