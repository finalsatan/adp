"""phase3：内置入库方案（P3-06）

Revision ID: 0004_builtin_schemes
Revises: 0003_phase3
Create Date: 2026-10-25

按固定 ID 写入每种文件类型的内置方案（只读）。配置数据定义在 `app/kb/schemes.py`
的 `BUILTIN_SCHEMES`，迁移与测试共用一份。本平台只做向前迁移，不实现 downgrade。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.kb.schemes import BUILTIN_SCHEMES

# revision identifiers, used by Alembic.
revision: str = "0004_builtin_schemes"
down_revision: str | None = "0003_phase3"
branch_labels: str | None = None
depends_on: str | None = None

_BUILTIN_IDS = tuple(row["id"] for row in BUILTIN_SCHEMES)


def upgrade() -> None:
    # 迁移只跑一次，但测试库会 reset；重复执行前先按固定 ID 清掉同 ID 行。
    ids = ", ".join(str(value) for value in _BUILTIN_IDS)
    op.execute(f"DELETE FROM ingest_schemes WHERE id IN ({ids})")

    schemes_table = sa.table(
        "ingest_schemes",
        sa.column("id", sa.BigInteger),
        sa.column("builtin", sa.Boolean),
        sa.column("resource_id", sa.BigInteger),
        sa.column("file_type", sa.Text),
        sa.column("name", sa.Text),
        sa.column("description", sa.Text),
        sa.column("config", postgresql.JSONB),
        sa.column("version", sa.Integer),
    )
    op.bulk_insert(
        schemes_table,
        [
            {
                "id": row["id"],
                "builtin": True,
                "resource_id": None,
                "file_type": row["file_type"],
                "name": row["name"],
                "description": row["description"],
                "config": row["config"],
                "version": 1,
            }
            for row in BUILTIN_SCHEMES
        ],
    )
    # 固定 ID 插入不会推进自增序列；第五期加自定义方案时不能占用 1–12。
    op.execute(
        "SELECT setval("
        "pg_get_serial_sequence('ingest_schemes', 'id'), "
        "(SELECT max(id) FROM ingest_schemes))"
    )


def downgrade() -> None:
    raise NotImplementedError("本平台只做向前迁移")
