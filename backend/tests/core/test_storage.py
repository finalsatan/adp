"""对象存储封装：连 `make dev-up` 起的 RustFS（P3-02）。"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import AsyncIterator

import pytest_asyncio

from app.core.config import Settings
from app.core.storage import (
    Storage,
    doc_file_key,
    doc_prefix,
    images_prefix,
    kb_original_key,
    kb_prefix,
    parsed_key,
    trial_prefix,
)


@pytest_asyncio.fixture
async def storage(settings: Settings) -> Storage:
    return Storage(settings)


@pytest_asyncio.fixture
async def prefix(settings: Settings) -> AsyncIterator[str]:
    """每个测试用独立前缀，结束后清掉，避免污染开发桶。"""
    value = f"test-p3-02/{uuid.uuid4().hex}/"
    yield value
    await Storage(settings).delete_prefix(value)


async def test_put_get_exists(storage: Storage, prefix: str) -> None:
    key = f"{prefix}bytes.bin"
    await storage.put_bytes(key, b"hello rustfs")
    assert await storage.exists(key) is True
    assert await storage.get_bytes(key) == b"hello rustfs"


async def test_exists_missing(storage: Storage, prefix: str) -> None:
    assert await storage.exists(f"{prefix}missing.bin") is False


async def test_upload_stream_large_file(storage: Storage, prefix: str) -> None:
    # ≥ 5MB，验证流式上传（boto3 分片）能完整读回
    data = b"adp-p3-02-" * (600 * 1024)  # 约 6MB
    key = f"{prefix}large.bin"
    with tempfile.NamedTemporaryFile() as handle:
        handle.write(data)
        handle.flush()
        handle.seek(0)
        await storage.upload_stream(key, handle, content_type="application/octet-stream")
    assert await storage.exists(key) is True
    assert await storage.get_bytes(key) == data


async def test_get_object_range(storage: Storage, prefix: str) -> None:
    key = f"{prefix}range.txt"
    await storage.put_bytes(key, b"0123456789")
    body, content_length, content_range = await storage.get_object(key, byte_range="bytes=2-5")
    assert body.read() == b"2345"
    assert content_length == 4
    assert content_range == "bytes 2-5/10"


async def test_copy(storage: Storage, prefix: str) -> None:
    source = f"{prefix}source.txt"
    dest = f"{prefix}dest.txt"
    await storage.put_bytes(source, b"copy me")
    await storage.copy(source, dest)
    assert await storage.get_bytes(dest) == b"copy me"


async def test_delete_prefix(storage: Storage, prefix: str) -> None:
    await storage.put_bytes(f"{prefix}a.bin", b"1")
    await storage.put_bytes(f"{prefix}b.bin", b"2")
    assert await storage.delete_prefix(prefix) == 2
    assert await storage.exists(f"{prefix}a.bin") is False
    assert await storage.exists(f"{prefix}b.bin") is False


async def test_check(storage: Storage) -> None:
    # 桶存在且可访问时不抛异常
    await storage.check()


def test_key_builders() -> None:
    assert kb_original_key(1, 2, "pdf") == "kb/1/docs/2/original.pdf"
    assert doc_prefix(1, 2) == "kb/1/docs/2/"
    assert doc_file_key(1, 2, "converted.docx") == "kb/1/docs/2/converted.docx"
    assert parsed_key(1, 2, "doc.json") == "kb/1/docs/2/parsed/original/doc.json"
    assert parsed_key(1, 2, "doc.md", edited=True) == "kb/1/docs/2/parsed/edited/doc.md"
    assert images_prefix(1, 2) == "kb/1/docs/2/images/"
    assert kb_prefix(1) == "kb/1/"
    assert trial_prefix(7) == "tmp/trial/7/"
