"""用户与组织表。"""

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("orgs.id"), nullable=True
    )
    path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    remark: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id", use_alter=True), nullable=True
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    deleted_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.Index("ix_orgs_path", "path", postgresql_ops={"path": "text_pattern_ops"}),
        sa.Index("ix_orgs_parent_id", "parent_id"),
        # 同一上级下组织不重名；顶级组织（parent_id 为空）之间也不重名。
        sa.Index(
            "uq_orgs_parent_name",
            sa.func.coalesce(sa.column("parent_id"), sa.literal(0)),
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(sa.Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    email: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    remark: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="active")
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    org_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("orgs.id"), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    token_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    last_login_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.Index("uq_users_username", "username", unique=True),
        sa.Index("ix_users_org_id", "org_id"),
        sa.Index("ix_users_role", "role"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="status"),
        sa.CheckConstraint(
            "role IN ('sys_admin', 'org_admin', 'builder', 'end_user')", name="role"
        ),
        # 系统管理员不属于组织，其他角色必须属于一个组织。
        sa.CheckConstraint(
            "(role = 'sys_admin') = (org_id IS NULL)",
            name="sys_admin_org",
        ),
    )
