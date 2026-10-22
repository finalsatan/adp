"""数据库初始化：扩展、kb_index schema、两个账号的权限。"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession


async def test_extensions_and_kb_index_schema(session: AsyncSession) -> None:
    extensions = {row[0] for row in await session.execute(text("SELECT extname FROM pg_extension"))}
    assert {"vector", "pg_search"} <= extensions

    schemas = {
        row[0]
        for row in await session.execute(
            text("SELECT schema_name FROM information_schema.schemata")
        )
    }
    assert "kb_index" in schemas


async def test_app_account_cannot_create_table_in_public(session: AsyncSession) -> None:
    with pytest.raises(ProgrammingError):
        await session.execute(text("CREATE TABLE public.p1_01_forbidden (id int)"))
    await session.rollback()


async def test_app_account_can_create_table_in_kb_index(session: AsyncSession) -> None:
    await session.execute(text("CREATE TABLE kb_index.p1_01_probe (id int)"))
    await session.execute(text("DROP TABLE kb_index.p1_01_probe"))
    await session.commit()
