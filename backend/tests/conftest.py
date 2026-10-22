"""测试夹具：连真实的 PostgreSQL（adp_test）和 Redis（15 号库）。"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import Settings
from app.core.redis import create_redis
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://adp_app:adp_app_pw@localhost:55432/adp_test",
)
TEST_MIGRATION_DATABASE_URL = os.environ.get(
    "TEST_MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://adp_migrator:adp_migrator_pw@localhost:55432/adp_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL=TEST_DATABASE_URL,
        MIGRATION_DATABASE_URL=TEST_MIGRATION_DATABASE_URL,
        REDIS_URL=TEST_REDIS_URL,
        JWT_SECRET="test-secret",
    )


@pytest.fixture(scope="session", autouse=True)
def _migrate(settings: Settings) -> None:
    """会话开始时用迁移账号对 adp_test 执行 alembic upgrade head。"""
    env = os.environ.copy()
    env.update(
        DATABASE_URL=settings.DATABASE_URL,
        MIGRATION_DATABASE_URL=settings.MIGRATION_DATABASE_URL,
        REDIS_URL=settings.REDIS_URL,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
    )


@pytest_asyncio.fixture(scope="session")
async def app(settings: Settings):
    application = create_app(settings)
    yield application
    await application.state.redis.aclose()
    await application.state.engine.dispose()


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def session(app) -> AsyncIterator[AsyncSession]:
    async with app.state.sessionmaker() as session:
        yield session


@pytest_asyncio.fixture
async def redis(app):
    yield app.state.redis


@pytest_asyncio.fixture(autouse=True)
async def _clean_after_test(settings: Settings) -> AsyncIterator[None]:
    """每个测试结束后清空 public 下的业务表和测试 Redis。"""
    yield
    engine: AsyncEngine = create_async_engine(settings.MIGRATION_DATABASE_URL)
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        tables = [row[0] for row in result]
        if tables:
            quoted = ", ".join(f'public."{name}"' for name in tables)
            await conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    await engine.dispose()

    client = create_redis(settings.REDIS_URL)
    await client.flushdb()
    await client.aclose()
