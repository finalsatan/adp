"""审计日志表。应用账号只有 INSERT、SELECT 权限（迁移里 REVOKE）。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    # 审计表独立，不对外键，避免其他表变动影响日志。
    actor_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    actor_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor_org_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    actor_org_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    target_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    target_desc: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    target_org_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    target_org_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    result: Mapped[str] = mapped_column(sa.Text, nullable=False)
    ip: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        sa.CheckConstraint("result IN ('success', 'failure')", name="result"),
        sa.Index("ix_audit_logs_at", sa.text("at DESC")),
        sa.Index("ix_audit_logs_actor_org_id", "actor_org_id"),
        sa.Index("ix_audit_logs_target_org_id", "target_org_id"),
        sa.Index("ix_audit_logs_action", "action"),
    )
