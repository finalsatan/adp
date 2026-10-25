"""P3-09 流水线测试夹具：真实 PostgreSQL / Redis / RustFS + 本地假解析服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.storage import kb_original_key
from app.kb.parser_client import ParserClient
from app.kb.tables import Document, IngestScheme
from tests.fake_parser.server import create_app
from tests.worker import make_kb


@pytest.fixture
def fake_parser_app(settings: Settings) -> FastAPI:
    """每个测试一个假解析服务实例，内存状态互不影响。"""
    return create_app(settings)


def _unique_doc_id() -> int:
    """给文档一个随机的大 id，让对象存储的键在不同测试之间不重复。

    RustFS 的前缀删除是最终一致的，清理上一个测试的对象时可能误删本次测试刚写入的
    同名键；随机 id 让每次测试的键都不同，清理就只会删到上一个测试自己的对象。
    """
    return (uuid4().int & ((1 << 63) - 1)) or 1


@pytest_asyncio.fixture(autouse=True)
async def _clean_kb_objects(app):
    """对象存储不参与数据库的清空，测试前后删掉 `kb/` 前缀，避免遗留对象。"""
    await app.state.storage.delete_prefix("kb/")
    yield
    await app.state.storage.delete_prefix("kb/")


@pytest_asyncio.fixture
async def parser_client(settings: Settings, fake_parser_app: FastAPI):
    """指向假解析服务（进程内 ASGI）的解析服务客户端。"""
    client = ParserClient(settings, transport=httpx.ASGITransport(app=fake_parser_app))
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def make_ingest_document(app, session: AsyncSession):
    """建知识库、入库方案、文档，并把原文件写进对象存储。

    `parse_params` 里的键会并进方案快照的 `parse` 下（假解析服务据此控制行为）。
    """

    async def _make(
        *,
        file_type: str = "pdf",
        ext: str | None = None,
        engine: str = "docling",
        parse_params: dict[str, Any] | None = None,
        run_id: str | None = "run-1",
        name: str | None = None,
    ) -> SimpleNamespace:
        resolved_ext = ext or file_type
        kb, user_id = await make_kb(session, f"流水线库-{file_type}-{uuid4().hex[:6]}")
        config: dict[str, Any] = {
            "schema_version": 1,
            "file_type": file_type,
            "parse": {"engine": engine},
            "chunk": {"mode": "auto"},
        }
        scheme = IngestScheme(
            builtin=True,
            file_type=file_type,
            name=f"测试方案-{uuid4().hex[:6]}",
            config=config,
        )
        session.add(scheme)
        await session.flush()

        snapshot: dict[str, Any] | None = None
        if parse_params:
            snapshot = {**config, "parse": {**config["parse"], **parse_params}}
        doc = Document(
            id=_unique_doc_id(),
            kb_id=kb.id,
            folder_id=None,
            name=name or f"文档.{resolved_ext}",
            file_type=file_type,
            ext=resolved_ext,
            size=64,
            sha256=uuid4().hex * 2,
            status="parsing",
            queued_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
            run_id=run_id,
            scheme_id=scheme.id,
            parse_snapshot=snapshot,
            uploaded_by=user_id,
            run_by=user_id,
        )
        session.add(doc)
        await session.commit()
        await app.state.storage.put_bytes(
            kb_original_key(kb.id, doc.id, resolved_ext), b"fake original content"
        )
        return SimpleNamespace(
            kb_id=kb.id,
            doc_id=doc.id,
            run_id=run_id,
            scheme_id=scheme.id,
            user_id=user_id,
            ext=resolved_ext,
            file_type=file_type,
        )

    return _make
