"""phase1：第一期全部业务表

Revision ID: 0001_phase1
Revises:
Create Date: 2026-10-22

本平台只做向前迁移，不实现 downgrade。
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_phase1"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_name", sa.Text(), nullable=False),
        sa.Column("actor_org_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_org_name", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("target_desc", sa.Text(), nullable=True),
        sa.Column("target_org_id", sa.BigInteger(), nullable=True),
        sa.Column("target_org_name", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("result IN ('success', 'failure')", name=op.f("ck_audit_logs_result")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index("ix_audit_logs_actor_org_id", "audit_logs", ["actor_org_id"], unique=False)
    op.create_index("ix_audit_logs_at", "audit_logs", [sa.literal_column("at DESC")], unique=False)
    op.create_index("ix_audit_logs_target_org_id", "audit_logs", ["target_org_id"], unique=False)

    op.create_table(
        "orgs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["orgs.id"], name=op.f("fk_orgs_parent_id_orgs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orgs")),
    )
    op.create_index("ix_orgs_parent_id", "orgs", ["parent_id"], unique=False)
    op.create_index(
        "ix_orgs_path",
        "orgs",
        ["path"],
        unique=False,
        postgresql_ops={"path": "text_pattern_ops"},
    )
    op.create_index(
        "uq_orgs_parent_name",
        "orgs",
        [sa.literal_column("coalesce(parent_id, 0)"), "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
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
            "(role = 'sys_admin') = (org_id IS NULL)", name=op.f("ck_users_sys_admin_org")
        ),
        sa.CheckConstraint(
            "role IN ('sys_admin', 'org_admin', 'builder', 'end_user')", name=op.f("ck_users_role")
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_users_status")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_users_created_by_users")
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name=op.f("fk_users_org_id_orgs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index("ix_users_org_id", "users", ["org_id"], unique=False)
    op.create_index("ix_users_role", "users", ["role"], unique=False)
    op.create_index("uq_users_username", "users", ["username"], unique=True)

    # orgs 与 users 互相引用，orgs.created_by 的外键在这里补建。
    op.create_foreign_key(op.f("fk_orgs_created_by_users"), "orgs", "users", ["created_by"], ["id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_refresh_tokens_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
    )
    op.create_index("ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"], unique=False)
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    op.create_index("uq_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)

    op.create_table(
        "resources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="enabled", nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('enabled', 'disabled')", name=op.f("ck_resources_status")),
        sa.CheckConstraint(
            "visibility IN ('private', 'org', 'platform')", name=op.f("ck_resources_visibility")
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name=op.f("fk_resources_org_id_orgs")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_resources_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resources")),
    )
    op.create_index("ix_resources_org_id", "resources", ["org_id"], unique=False)
    op.create_index("ix_resources_owner_id", "resources", ["owner_id"], unique=False)
    op.create_index("ix_resources_type", "resources", ["type"], unique=False)
    op.create_index(
        "uq_resources_type_org_name",
        "resources",
        ["type", "org_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "resource_members",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["users.id"], name=op.f("fk_resource_members_added_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resources.id"],
            name=op.f("fk_resource_members_resource_id_resources"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_resource_members_user_id_users")
        ),
        sa.PrimaryKeyConstraint("resource_id", "user_id", name=op.f("pk_resource_members")),
    )
    op.create_index("ix_resource_members_user_id", "resource_members", ["user_id"], unique=False)

    op.create_table(
        "resource_refs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("ref_resource_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_type IN ('model', 'resource')", name=op.f("ck_resource_refs_target_type")
        ),
        sa.ForeignKeyConstraint(
            ["ref_resource_id"],
            ["resources.id"],
            name=op.f("fk_resource_refs_ref_resource_id_resources"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_refs")),
    )
    op.create_index(
        "ix_resource_refs_ref_resource_id", "resource_refs", ["ref_resource_id"], unique=False
    )
    op.create_index(
        "ix_resource_refs_target", "resource_refs", ["target_type", "target_id"], unique=False
    )
    op.create_index(
        "uq_resource_refs_target_ref",
        "resource_refs",
        ["target_type", "target_id", "ref_resource_id"],
        unique=True,
    )

    # 审计日志只增不改：收回应用账号的 UPDATE、DELETE、TRUNCATE。
    app_role = os.environ.get("ADP_APP_ROLE", "adp_app")
    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM "{app_role}"')


def downgrade() -> None:
    raise NotImplementedError("本平台只做向前迁移")
