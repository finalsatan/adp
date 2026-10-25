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
from app.settings import service as settings_service
from tests.factories import org_tree  # noqa: F401  # 注册 org_tree 夹具

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
# 对象存储（P3-02）：连 `make dev-up` 起的 RustFS 开发容器。
TEST_S3_ENDPOINT = os.environ.get("TEST_S3_ENDPOINT", "http://localhost:59000")
TEST_S3_BUCKET = os.environ.get("TEST_S3_BUCKET", "adp")
TEST_S3_ACCESS_KEY = os.environ.get("TEST_S3_ACCESS_KEY", "adpdev")
TEST_S3_SECRET_KEY = os.environ.get("TEST_S3_SECRET_KEY", "adpdevsecret")
# 解析服务客户端（P3-02）默认值；测试用 httpx.MockTransport，不会真连。
TEST_PARSER_URL = os.environ.get("TEST_PARSER_URL", "http://localhost:59001")
TEST_PARSER_TOKEN = os.environ.get("TEST_PARSER_TOKEN", "test-parser-token")
# tiktoken 编码文件的离线缓存目录（P2-05）：测试环境与镜像保持一致。
TEST_TIKTOKEN_CACHE_DIR = os.environ.get(
    "TIKTOKEN_CACHE_DIR", str(Path.home() / ".cache" / "adp-tiktoken")
)
os.environ["TIKTOKEN_CACHE_DIR"] = TEST_TIKTOKEN_CACHE_DIR
# 固定的测试主密钥（Base64 编码的 32 字节），不用于生产。
TEST_MASTER_KEY = "dGVzdC1tYXN0ZXIta2V5LTAxMjM0NTY3ODlhYmNkZWY="


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL=TEST_DATABASE_URL,
        MIGRATION_DATABASE_URL=TEST_MIGRATION_DATABASE_URL,
        REDIS_URL=TEST_REDIS_URL,
        JWT_SECRET="test-secret-for-p1-05-tests-0123456789",
        MASTER_KEY=TEST_MASTER_KEY,
        S3_ENDPOINT=TEST_S3_ENDPOINT,
        S3_BUCKET=TEST_S3_BUCKET,
        S3_ACCESS_KEY=TEST_S3_ACCESS_KEY,
        S3_SECRET_KEY=TEST_S3_SECRET_KEY,
        PARSER_URL=TEST_PARSER_URL,
        PARSER_TOKEN=TEST_PARSER_TOKEN,
    )


@pytest.fixture(scope="session", autouse=True)
def _migrate(settings: Settings) -> None:
    """会话开始时用迁移账号对 adp_test 执行 alembic upgrade head。"""
    env = os.environ.copy()
    env.update(
        DATABASE_URL=settings.DATABASE_URL,
        MIGRATION_DATABASE_URL=settings.MIGRATION_DATABASE_URL,
        REDIS_URL=settings.REDIS_URL,
        MASTER_KEY=settings.MASTER_KEY,
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
    settings_service.clear_cache()
