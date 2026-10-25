"""上传文档：流式写对象存储、同名 / 重复处理、文件夹与权限（P3-07）。"""

from __future__ import annotations

import hashlib
import json

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.core.storage import kb_original_key
from app.kb.schemes import BUILTIN_SCHEMES
from app.kb.tables import Document, IngestScheme, KbFolder, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from app.settings import service as settings_service
from tests.factories import auth_cookies, make_org, make_user

UPLOAD_URL = "/api/v1/kbs/{kb_id}/documents"


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def seed_builtin_schemes(session: AsyncSession) -> None:
    """每个测试后 TRUNCATE 会清掉迁移写入的内置方案，测试前按固定 ID 重新写入。"""
    ids = [row["id"] for row in BUILTIN_SCHEMES]
    await session.execute(sa.delete(IngestScheme).where(IngestScheme.id.in_(ids)))
    for row in BUILTIN_SCHEMES:
        session.add(
            IngestScheme(
                id=row["id"],
                builtin=True,
                resource_id=None,
                file_type=row["file_type"],
                name=row["name"],
                description=row["description"],
                config=row["config"],
                version=1,
            )
        )
    await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _builtin_schemes(session: AsyncSession) -> None:
    await seed_builtin_schemes(session)


@pytest_asyncio.fixture(autouse=True)
async def _clean_objects(app):
    """对象存储不参与数据库清空，测试前后删掉 `kb/` 前缀，避免同 id 串数据。"""
    await app.state.storage.delete_prefix("kb/")
    yield
    await app.state.storage.delete_prefix("kb/")


@pytest.fixture
def dispatch_calls(monkeypatch):
    """拦截 P3-08 的调度投递，返回记录到的调用。"""
    from app.worker.celery_app import celery_app

    calls: list[tuple[str, list[int] | None, dict]] = []

    def fake(name, args=None, **kwargs):
        calls.append((name, args, kwargs))

    monkeypatch.setattr(celery_app, "send_task", fake)
    return calls


async def make_kb(
    session: AsyncSession,
    *,
    org,
    user,
    name: str = "上传知识库",
    embedding_max_input: int = 8192,
) -> KnowledgeBase:
    resource = Resource(type="kb", name=name, org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    model = Model(
        name=f"向量模型-{name}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=1024,
        embedding_max_input=embedding_max_input,
        created_by=user.id,
    )
    session.add(model)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind="doc",
        embedding_model_id=model.id,
        embedding_dim=1024,
        index_table=f"kb_index.kb_{resource.id}",
    )
    session.add(kb)
    await session.commit()
    return kb


async def make_folder(session: AsyncSession, kb_id: int, name: str) -> KbFolder:
    folder = KbFolder(kb_id=kb_id, parent_id=None, path="", name=name)
    session.add(folder)
    await session.flush()
    folder.path = f"/{folder.id}/"
    await session.commit()
    return folder


def _multipart(
    items: list[tuple[str, bytes]],
    relative_paths: list[str],
    *,
    schemes: dict | None = None,
    resolutions: dict | None = None,
    folder_id: int | None = None,
) -> list[tuple[str, tuple]]:
    parts: list[tuple[str, tuple]] = []
    for name, content in items:
        parts.append(("files", (name, content, "application/octet-stream")))
    for relative_path in relative_paths:
        parts.append(("relative_paths", (None, relative_path)))
    parts.append(("schemes", (None, json.dumps(schemes or {}))))
    parts.append(("resolutions", (None, json.dumps(resolutions or {}))))
    if folder_id is not None:
        parts.append(("folder_id", (None, str(folder_id))))
    return parts


async def test_upload_two_files_and_dispatch(
    app, client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    org = await make_org(session, "上传组织")
    owner = await make_user(session, Role.builder, org, username="upload_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    content_a = b"hello upload"
    content_b = b"# markdown title"
    response = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart(
            [("a.txt", content_a), ("b.md", content_b)],
            ["", ""],
        ),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["items"]) == 2
    assert body["skipped"] == []

    by_name = {item["name"]: item for item in body["items"]}
    for name, content, ext in (
        ("a.txt", content_a, "txt"),
        ("b.md", content_b, "md"),
    ):
        item = by_name[name]
        assert item["status"] == "queued"
        assert item["size"] == len(content)
        doc = await session.get(Document, item["id"])
        assert doc is not None
        assert doc.sha256 == hashlib.sha256(content).hexdigest()
        stored = await app.state.storage.get_bytes(kb_original_key(kb.id, item["id"], ext))
        assert stored == content

    # 投递了 P3-08 的调度任务。
    assert len(dispatch_calls) == 1
    name, args, kwargs = dispatch_calls[0]
    assert name == "app.worker.tasks.dispatch_kb"
    assert args == [kb.id]
    assert kwargs["queue"] == "ingest"


async def test_upload_large_file_hash(
    app, client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    """超过 8MB 触发 boto3 分片上传，SHA-256 与大小仍正确（读多遍不重复计数）。"""
    org = await make_org(session, "大文件组织")
    owner = await make_user(session, Role.builder, org, username="large_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    content = b"0123456789abcdef" * (9 * 1024 * 1024 // 16)
    response = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart([("big.txt", content)], [""]),
    )
    assert response.status_code == 201, response.text
    item = response.json()["items"][0]
    assert item["size"] == len(content)
    doc = await session.get(Document, item["id"])
    assert doc is not None
    assert doc.sha256 == hashlib.sha256(content).hexdigest()
    stored = await app.state.storage.get_bytes(kb_original_key(kb.id, item["id"], "txt"))
    assert stored == content


async def test_upload_survives_dispatch_failure(
    client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """broker 临时不可用时上传仍成功（文档已落库，等 beat 补发）。"""
    from app.worker.celery_app import celery_app

    def boom(*_args, **_kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(celery_app, "send_task", boom)

    org = await make_org(session, "投递失败组织")
    owner = await make_user(session, Role.builder, org, username="dispatch_fail_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    response = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart([("a.txt", b"data")], [""]),
    )
    assert response.status_code == 201, response.text
    assert response.json()["items"][0]["status"] == "queued"


async def test_upload_replace_keeps_metadata_and_folder(
    app, client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    org = await make_org(session, "替换组织")
    owner = await make_user(session, Role.builder, org, username="replace_owner")
    kb = await make_kb(session, org=org, user=owner)
    folder = await make_folder(session, kb.id, "甲")
    login_as(client, owner)

    first = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart([("a.txt", b"old content")], [""], folder_id=folder.id),
    )
    assert first.status_code == 201, first.text
    doc_id = first.json()["items"][0]["id"]

    doc = await session.get(Document, doc_id)
    assert doc is not None
    doc.doc_metadata = {"部门": "研发"}
    await session.commit()

    second = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart(
            [("a.txt", b"new content")],
            [""],
            resolutions={"name_conflict": {"a.txt": "replace"}},
            folder_id=folder.id,
        ),
    )
    assert second.status_code == 201, second.text
    item = second.json()["items"][0]
    assert item["id"] == doc_id
    assert item["name"] == "a.txt"
    assert item["status"] == "queued"
    assert item["metadata"] == {"部门": "研发"}
    assert item["folder"]["id"] == folder.id
    assert item["size"] == len(b"new content")
    replaced = await session.get(Document, doc_id)
    assert replaced is not None
    await session.refresh(replaced)
    assert replaced.sha256 == hashlib.sha256(b"new content").hexdigest()

    count = await session.scalar(
        sa.select(sa.func.count()).select_from(Document).where(Document.kb_id == kb.id)
    )
    assert count == 1
    stored = await app.state.storage.get_bytes(kb_original_key(kb.id, doc_id, "txt"))
    assert stored == b"new content"


async def test_upload_keep_creates_numbered_copy(
    client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    org = await make_org(session, "保留两份组织")
    owner = await make_user(session, Role.builder, org, username="keep_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    first = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart([("a.txt", b"one")], [""]),
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart(
            [("a.txt", b"two")],
            [""],
            resolutions={"name_conflict": {"a.txt": "keep"}},
        ),
    )
    assert second.status_code == 201, second.text
    assert second.json()["items"][0]["name"] == "a(1).txt"

    names = set(
        (await session.execute(sa.select(Document.name).where(Document.kb_id == kb.id)))
        .scalars()
        .all()
    )
    assert names == {"a.txt", "a(1).txt"}


async def test_upload_duplicate_skip_and_upload(
    client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    org = await make_org(session, "重复组织")
    owner = await make_user(session, Role.builder, org, username="dup_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    content = b"same content"
    first = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart([("a.txt", content)], [""]),
    )
    assert first.status_code == 201, first.text

    skipped = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart(
            [("b.txt", content)],
            ["skipdir/b.txt"],
            resolutions={"duplicate": {"b.txt": "skip"}},
        ),
    )
    assert skipped.status_code == 201, skipped.text
    assert skipped.json()["items"] == []
    assert skipped.json()["skipped"][0]["name"] == "b.txt"
    # 被跳过的文件不建目录。
    folder_count = await session.scalar(
        sa.select(sa.func.count()).select_from(KbFolder).where(KbFolder.kb_id == kb.id)
    )
    assert folder_count == 0

    uploaded = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart(
            [("c.txt", content)],
            [""],
            resolutions={"duplicate": {"c.txt": "upload"}},
        ),
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["items"][0]["name"] == "c.txt"

    names = set(
        (await session.execute(sa.select(Document.name).where(Document.kb_id == kb.id)))
        .scalars()
        .all()
    )
    assert names == {"a.txt", "c.txt"}


async def test_upload_folder_structure(
    client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    org = await make_org(session, "文件夹上传组织")
    owner = await make_user(session, Role.builder, org, username="folder_upload_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    response = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart([("c.txt", b"deep")], ["root/sub/deep/c.txt"]),
    )
    assert response.status_code == 201, response.text
    item = response.json()["items"][0]
    assert item["folder"] is not None

    folders = (
        (await session.execute(sa.select(KbFolder).where(KbFolder.kb_id == kb.id))).scalars().all()
    )
    by_name = {folder.name: folder for folder in folders}
    assert set(by_name) == {"root", "sub", "deep"}
    assert by_name["root"].path == f"/{by_name['root'].id}/"
    assert by_name["sub"].path == f"/{by_name['root'].id}/{by_name['sub'].id}/"
    assert (
        by_name["deep"].path == f"/{by_name['root'].id}/{by_name['sub'].id}/{by_name['deep'].id}/"
    )
    assert item["folder"]["id"] == by_name["deep"].id
    assert item["folder"]["path"] == by_name["deep"].path


async def test_upload_rejects_fake_type_and_oversize(
    client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    org = await make_org(session, "拒绝组织")
    owner = await make_user(session, Role.builder, org, username="reject_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    # 伪造扩展名：把可执行文件改名 .pdf，按文件头识别为 exe → 整批拒绝。
    fake = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart([("evil.pdf", b"MZ\x90\x00\x03\x00\x00\x00")], [""]),
    )
    assert fake.status_code == 422, fake.text
    assert fake.json()["code"] == "UNSUPPORTED_FILE_TYPE"

    await settings_service.update_settings(session, {"upload": {"max_file_size_mb": 1}}, owner.id)
    oversize = await client.post(
        UPLOAD_URL.format(kb_id=kb.id),
        files=_multipart([("big.txt", b"x" * (2 * 1024 * 1024))], [""]),
    )
    assert oversize.status_code == 413, oversize.text
    assert oversize.json()["code"] == "FILE_TOO_LARGE"

    count = await session.scalar(
        sa.select(sa.func.count()).select_from(Document).where(Document.kb_id == kb.id)
    )
    assert count == 0
    assert dispatch_calls == []


async def test_upload_permissions(client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "上传权限集团")
    rd = await make_org(session, "上传权限研发", parent=group)
    product = await make_org(session, "上传权限产品", parent=group)
    owner = await make_user(session, Role.builder, rd, username="upload_perm_owner")
    viewer = await make_user(session, Role.builder, rd, username="upload_perm_viewer")
    outsider = await make_user(session, Role.builder, product, username="upload_perm_out")
    kb = await make_kb(session, org=rd, user=owner)
    parts = _multipart([("a.txt", b"data")], [""])

    login_as(client, viewer)
    denied = await client.post(UPLOAD_URL.format(kb_id=kb.id), files=parts)
    assert denied.status_code == 403

    login_as(client, outsider)
    hidden = await client.post(UPLOAD_URL.format(kb_id=kb.id), files=parts)
    assert hidden.status_code == 404
