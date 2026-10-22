"""统一资源登记表、协作者与引用关系。"""

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    org_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("orgs.id"), nullable=False)
    owner_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("users.id"), nullable=False)
    visibility: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="enabled")
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    deleted_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.CheckConstraint("visibility IN ('private', 'org', 'platform')", name="visibility"),
        sa.CheckConstraint("status IN ('enabled', 'disabled')", name="status"),
        sa.Index("ix_resources_org_id", "org_id"),
        sa.Index("ix_resources_owner_id", "owner_id"),
        sa.Index("ix_resources_type", "type"),
        # 同类资源在同一组织内不重名（未删除的才算）。
        sa.Index(
            "uq_resources_type_org_name",
            "type",
            "org_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )


class ResourceMember(Base):
    __tablename__ = "resource_members"

    resource_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("resources.id"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("users.id"), primary_key=True)
    added_by: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("users.id"), nullable=False)
    added_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (sa.Index("ix_resource_members_user_id", "user_id"),)


class ResourceRef(Base):
    __tablename__ = "resource_refs"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    ref_resource_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("resources.id"), nullable=False
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.CheckConstraint("target_type IN ('model', 'resource')", name="target_type"),
        sa.Index(
            "uq_resource_refs_target_ref",
            "target_type",
            "target_id",
            "ref_resource_id",
            unique=True,
        ),
        sa.Index("ix_resource_refs_target", "target_type", "target_id"),
        sa.Index("ix_resource_refs_ref_resource_id", "ref_resource_id"),
    )
