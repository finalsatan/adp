"""应用（M3）业务表（应用技术方案 10、5.2、11）。

名称、描述、所属组织、可见范围、启停状态在 resources（type = app），本模块只放应用
自身的草稿、版本与使用范围名单。对话相关表见 app/chat/tables.py。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

APP_TYPES = ("kb_qa",)
USAGE_SCOPES = ("platform", "orgs", "users")
USAGE_TARGET_TYPES = ("org", "user")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class App(Base):
    """应用。id 同时是 resources.id 的外键；草稿与当前发布版本指向这里。"""

    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("resources.id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    app_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    draft_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    draft_revision: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    draft_updated_by: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    draft_updated_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # 与 app_versions.app_id 循环引用；use_alter 让 SQLAlchemy 用 ALTER TABLE 补外键。
    published_version_id: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey(
            "app_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_apps_published_version_id_app_versions",
        ),
        nullable=True,
    )
    usage_scope: Mapped[str] = mapped_column(sa.Text, nullable=False)

    __table_args__ = (
        sa.CheckConstraint(_in_list("app_type", APP_TYPES), name="app_type"),
        sa.CheckConstraint(_in_list("usage_scope", USAGE_SCOPES), name="usage_scope"),
    )


class AppVersion(Base):
    """发布版本。插入后不修改（应用技术方案 5.2）。"""

    __tablename__ = "app_versions"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    config_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    note: Mapped[str] = mapped_column(sa.Text, nullable=False)
    published_by: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    published_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.UniqueConstraint("app_id", "version_no", name="uq_app_versions_app_version_no"),
    )


class AppUsageTarget(Base):
    """使用范围名单。scope 为 platform 时没有行（应用技术方案 11）。"""

    __tablename__ = "app_usage_targets"

    app_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("apps.id", ondelete="CASCADE"), primary_key=True
    )
    target_type: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    target_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)

    __table_args__ = (
        sa.CheckConstraint(_in_list("target_type", USAGE_TARGET_TYPES), name="target_type"),
    )
