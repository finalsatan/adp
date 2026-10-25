"""文档列表、详情、编辑、重试、重新解析、删除与批量操作（P3-12）。

连真实 PostgreSQL、Redis、RustFS；重试 / 重新解析的断点续跑用 monkeypatch 把流水线
各步骤换成记录调用，验证已完成的步骤不重跑（知识库技术方案 8.3）。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import litellm
import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.core.storage import kb_original_key
from app.kb import pipeline, retrieval
from app.kb.index_table import create_index_table, index_table_name
from app.kb.schemas import RetrievalConfig
from app.kb.tables import (
    Chunk,
    Document,
    IngestScheme,
    KbFolder,
    KbMetadataField,
    KnowledgeBase,
)
from app.models.tables import Model
from app.resources.tables import Resource, ResourceMember
from app.worker.dispatch import RunCancelled
from tests.factories import auth_cookies, make_org, make_user
from tests.kb.test_retrieval import fake_embedding

SCHEME_CONFIG = {
    "schema_version": 1,
    "file_type": "pdf",
    "parse": {"engine": "docling"},
    "chunk": {"mode": "auto"},
}


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


@pytest_asyncio.fixture(autouse=True)
async def _clean_objects(app):
    """对象存储不参与数据库清空，测试前后删掉 `kb/` 前缀，避免遗留对象。"""
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
    session: AsyncSession, *, org, user, name: str | None = None, dim: int = 1024
) -> KnowledgeBase:
    resource = Resource(
        type="kb",
        name=name or f"文档库-{uuid.uuid4().hex[:6]}",
        org_id=org.id,
        owner_id=user.id,
        visibility="org",
    )
    session.add(resource)
    await session.flush()
    model = Model(
        name=f"向量模型-{uuid.uuid4().hex[:8]}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=dim,
        embedding_max_input=8192,
        created_by=user.id,
    )
    session.add(model)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind="doc",
        embedding_model_id=model.id,
        embedding_dim=dim,
        index_table=index_table_name(resource.id),
    )
    session.add(kb)
    await session.commit()
    return kb


async def make_scheme(
    session: AsyncSession, *, file_type: str = "pdf", name: str | None = None
) -> IngestScheme:
    scheme = IngestScheme(
        builtin=True,
        file_type=file_type,
        name=name or f"方案-{uuid.uuid4().hex[:6]}",
        config={**SCHEME_CONFIG, "file_type": file_type},
    )
    session.add(scheme)
    await session.commit()
    return scheme


async def make_doc(
    session: AsyncSession,
    kb: KnowledgeBase,
    user_id: int,
    *,
    name: str | None = None,
    status: str = "done",
    folder_id: int | None = None,
    scheme_id: int | None = None,
    metadata: dict | None = None,
    step: int | None = 6,
    failed_step: int | None = None,
    error_message: str | None = None,
    run_id: str | None = "run-1",
    queued_at: datetime | None = None,
    snapshot: dict | None = None,
    chunk_count: int = 0,
) -> Document:
    doc = Document(
        kb_id=kb.id,
        folder_id=folder_id,
        name=name or f"文档-{uuid.uuid4().hex[:6]}.pdf",
        file_type="pdf",
        ext="pdf",
        size=10,
        sha256=uuid.uuid4().hex * 2,
        status=status,
        step=step,
        failed_step=failed_step,
        error_message=error_message,
        run_id=run_id,
        queued_at=queued_at or datetime.now(UTC),
        scheme_id=scheme_id,
        parse_snapshot=snapshot if snapshot is not None else dict(SCHEME_CONFIG),
        doc_metadata=metadata or {},
        uploaded_by=user_id,
        run_by=user_id,
        chunk_count=chunk_count,
    )
    session.add(doc)
    await session.commit()
    return doc


async def make_folder(
    session: AsyncSession, kb_id: int, name: str, parent: KbFolder | None = None
) -> KbFolder:
    folder = KbFolder(kb_id=kb_id, parent_id=parent.id if parent else None, path="", name=name)
    session.add(folder)
    await session.flush()
    folder.path = (parent.path if parent else "/") + f"{folder.id}/"
    await session.commit()
    return folder


async def _index_active_count(session: AsyncSession, kb_id: int, doc_id: int) -> int:
    return int(
        await session.scalar(
            text(
                f"SELECT count(*) FROM {index_table_name(kb_id)} WHERE doc_id = :doc_id AND active"
            ),
            {"doc_id": doc_id},
        )
        or 0
    )


# ---- 权限 ----


async def test_list_and_detail_permissions(client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "权限集团")
    rd = await make_org(session, "权限研发", parent=group)
    other = await make_org(session, "权限产品", parent=group)
    owner = await make_user(session, Role.builder, rd, username="doc_perm_owner")
    peer = await make_user(session, Role.builder, rd, username="doc_perm_peer")
    collaborator = await make_user(session, Role.builder, rd, username="doc_perm_collab")
    outsider = await make_user(session, Role.builder, other, username="doc_perm_out")
    kb = await make_kb(session, org=rd, user=owner)
    doc = await make_doc(session, kb, owner.id)
    session.add(ResourceMember(resource_id=kb.id, user_id=collaborator.id, added_by=owner.id))
    await session.commit()

    # 创建人：能看能改。
    login_as(client, owner)
    assert (await client.get(f"/api/v1/kbs/{kb.id}/documents")).status_code == 200
    assert (await client.get(f"/api/v1/documents/{doc.id}")).status_code == 200
    patched = await client.patch(f"/api/v1/documents/{doc.id}", json={"enabled": False})
    assert patched.status_code == 200, patched.text

    # 同组织平级：能看，不能改。
    login_as(client, peer)
    assert (await client.get(f"/api/v1/documents/{doc.id}")).status_code == 200
    denied = await client.patch(f"/api/v1/documents/{doc.id}", json={"enabled": True})
    assert denied.status_code == 403
    for method, path, payload in (
        ("POST", f"/api/v1/documents/{doc.id}:retry", None),
        ("POST", f"/api/v1/documents/{doc.id}:reparse", {"mode": "reparse"}),
        ("DELETE", f"/api/v1/documents/{doc.id}", None),
    ):
        response = await client.request(method, path, json=payload)
        assert response.status_code == 403, response.text

    # 协作者：能编辑（不能改可见范围由 P1-12 保证，抽查一条）。
    login_as(client, collaborator)
    allowed = await client.patch(f"/api/v1/documents/{doc.id}", json={"enabled": True})
    assert allowed.status_code == 200, allowed.text
    visibility = await client.patch(
        f"/api/v1/kbs/{kb.id}/visibility", json={"visibility": "private"}
    )
    assert visibility.status_code == 403

    # 平级组织用户：看不到（404）。
    login_as(client, outsider)
    assert (await client.get(f"/api/v1/documents/{doc.id}")).status_code == 404


# ---- 列表筛选与排队位次 ----


async def test_list_filters_status_counts_and_queue_position(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "列表组织")
    owner = await make_user(session, Role.builder, org, username="doc_list_owner")
    kb = await make_kb(session, org=org, user=owner)
    root = await make_folder(session, kb.id, "根")
    sub = await make_folder(session, kb.id, "子", parent=root)
    scheme_a = await make_scheme(session, name="方案A")
    scheme_b = await make_scheme(session, name="方案B")
    field = KbMetadataField(kb_id=kb.id, name="部门", type="text")
    session.add(field)
    await session.commit()

    a = await make_doc(
        session,
        kb,
        owner.id,
        name="报告A.pdf",
        folder_id=root.id,
        scheme_id=scheme_a.id,
        metadata={"部门": "研发"},
    )
    b = await make_doc(
        session,
        kb,
        owner.id,
        name="报告B.pdf",
        status="failed",
        folder_id=sub.id,
        scheme_id=scheme_b.id,
        metadata={"部门": "市场"},
        failed_step=2,
        error_message="解析失败",
    )
    c = await make_doc(
        session,
        kb,
        owner.id,
        name="未分类.pdf",
        status="queued",
        scheme_id=scheme_a.id,
        step=None,
        queued_at=datetime.now(UTC),
    )
    d = await make_doc(
        session,
        kb,
        owner.id,
        name="处理中.pdf",
        status="parsing",
        folder_id=root.id,
        scheme_id=scheme_a.id,
        metadata={"部门": "研发"},
        step=2,
    )
    e = await make_doc(
        session,
        kb,
        owner.id,
        name="排队第二.pdf",
        status="queued",
        scheme_id=scheme_a.id,
        step=None,
        queued_at=datetime.now(UTC) + timedelta(seconds=5),
    )
    login_as(client, owner)
    url = f"/api/v1/kbs/{kb.id}/documents"

    # 文件夹含下级 / 不含下级。
    with_sub = (await client.get(url, params={"folder_id": root.id})).json()
    assert {item["id"] for item in with_sub["items"]} == {a.id, b.id, d.id}
    without_sub = (
        await client.get(url, params={"folder_id": root.id, "include_sub": False})
    ).json()
    assert {item["id"] for item in without_sub["items"]} == {a.id, d.id}

    # 未分类。
    uncategorized = (await client.get(url, params={"uncategorized": True})).json()
    assert {item["id"] for item in uncategorized["items"]} == {c.id, e.id}

    # 状态、方案、关键字、元数据。
    assert {
        item["id"] for item in (await client.get(url, params={"status": "failed"})).json()["items"]
    } == {b.id}
    assert {
        item["id"]
        for item in (await client.get(url, params={"scheme_id": scheme_b.id})).json()["items"]
    } == {b.id}
    assert {
        item["id"] for item in (await client.get(url, params={"keyword": "报告A"})).json()["items"]
    } == {a.id}
    metadata = json.dumps({"field_id": field.id, "operator": "eq", "value": "研发"})
    assert {
        item["id"]
        for item in (await client.get(url, params={"metadata": metadata})).json()["items"]
    } == {a.id, d.id}

    # 有人工修改：第三期恒为 false，edited=true 无结果。
    assert (await client.get(url, params={"edited": True})).json()["items"] == []
    assert len((await client.get(url, params={"edited": False})).json()["items"]) == 5

    # 全库状态计数不受分页和筛选影响。
    failed_page = (await client.get(url, params={"status": "failed"})).json()
    assert failed_page["total"] == 1
    counts = failed_page["status_counts"]
    assert counts["done"] == 1
    assert counts["failed"] == 1
    assert counts["queued"] == 2
    assert counts["parsing"] == 1

    # 排队位次按入队时间。
    queued = (await client.get(url, params={"status": "queued"})).json()["items"]
    positions = {item["id"]: item["queue_position"] for item in queued}
    assert positions[c.id] == 1
    assert positions[e.id] == 2

    # 人工修改标记与失败原因在字段里。
    detail = (await client.get(f"/api/v1/documents/{b.id}")).json()
    assert detail["edited"] is False
    assert detail["error_message"] == "解析失败"
    assert detail["processing"] is False
    assert isinstance(detail["parses"], list)
    assert isinstance(detail["parse_records"], list)
    assert detail["parse_snapshot"]["parse"]["engine"] == "docling"
    processing = (await client.get(f"/api/v1/documents/{d.id}")).json()
    assert processing["processing"] is True


# ---- 重试 ----


async def test_retry_only_failed_and_resumes_from_failed_step(
    app, client: AsyncClient, session: AsyncSession, dispatch_calls, monkeypatch
) -> None:
    org = await make_org(session, "重试组织")
    owner = await make_user(session, Role.builder, org, username="doc_retry_owner")
    kb = await make_kb(session, org=org, user=owner)
    failed = await make_doc(
        session,
        kb,
        owner.id,
        status="failed",
        step=2,
        failed_step=2,
        error_message="解析失败",
        run_id="old-run",
    )
    done = await make_doc(session, kb, owner.id, status="done", step=6)
    await app.state.storage.put_bytes(kb_original_key(kb.id, failed.id, "pdf"), b"original")
    login_as(client, owner)

    # 非失败文档不能重试。
    not_ready = await client.post(f"/api/v1/documents/{done.id}:retry")
    assert not_ready.status_code == 409
    assert not_ready.json()["code"] == "DOCUMENT_NOT_READY"

    retried = await client.post(f"/api/v1/documents/{failed.id}:retry")
    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["status"] == "queued"
    assert body["step"] == 2
    assert body["failed_step"] is None
    assert body["error_message"] is None

    await session.refresh(failed)
    assert failed.run_id != "old-run"
    assert failed.status == "queued"
    assert dispatch_calls and dispatch_calls[-1][1] == [kb.id]

    # 从 failed_step=2 继续：步骤 1 转换不重跑，步骤 2 及之后照跑。
    calls: list[str] = []

    async def fake_convert(*_args, **_kwargs):
        calls.append("convert")
        return "key", "pdf"

    async def fake_parse(*_args, **_kwargs):
        calls.append("parse")

    async def fake_chunk(*_args, **_kwargs):
        calls.append("chunk")

    async def fake_embed(*_args, **_kwargs):
        calls.append("embed")

    async def fake_activate(*_args, **_kwargs):
        calls.append("activate")

    monkeypatch_pipeline(
        monkeypatch,
        convert=fake_convert,
        parse=fake_parse,
        chunk=fake_chunk,
        embed=fake_embed,
        activate=fake_activate,
    )
    await pipeline.run_pipeline(
        app.state.sessionmaker,
        app.state.redis,
        failed.id,
        failed.run_id,
        settings=app.state.settings,
        storage=app.state.storage,
    )
    assert calls == ["parse", "chunk", "embed", "activate"]


def monkeypatch_pipeline(monkeypatch, *, convert, parse, chunk, embed, activate):
    """把 pipeline 的步骤函数换成记录调用的假实现（随 monkeypatch 自动还原）。"""
    monkeypatch.setattr(pipeline, "_step_convert", convert)
    monkeypatch.setattr(pipeline, "_step_parse", parse)
    monkeypatch.setattr(pipeline, "_step_chunk", chunk)
    monkeypatch.setattr(pipeline, "_step_embed", embed)
    monkeypatch.setattr(pipeline, "_step_activate", activate)


# ---- 重新解析 ----


async def test_reparse_modes_update_snapshot_and_start_step(
    app, client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    org = await make_org(session, "重新解析组织")
    owner = await make_user(session, Role.builder, org, username="doc_reparse_owner")
    kb = await make_kb(session, org=org, user=owner)
    old_scheme = await make_scheme(session, name="旧方案")
    new_scheme = await make_scheme(session, name="新方案")
    new_scheme.config = {
        **SCHEME_CONFIG,
        "parse": {"engine": "markitdown"},
        "chunk": {"mode": "length"},
    }
    await session.commit()
    doc = await make_doc(session, kb, owner.id, scheme_id=old_scheme.id, status="done")
    login_as(client, owner)

    # 重新解析：整套快照更新，断点 1。
    reparsed = await client.post(
        f"/api/v1/documents/{doc.id}:reparse",
        json={"mode": "reparse", "scheme_id": new_scheme.id},
    )
    assert reparsed.status_code == 200, reparsed.text
    body = reparsed.json()
    assert body["step"] == 1
    assert body["parse_edited"] is False
    assert body["chunk_edited_count"] == 0
    await session.refresh(doc)
    assert doc.parse_snapshot["parse"]["engine"] == "markitdown"
    assert doc.parse_snapshot["chunk"]["mode"] == "length"
    assert doc.scheme_id == new_scheme.id

    # 仅重新分块：解析设置不变，只换分块设置，断点 3。
    third_scheme = await make_scheme(session, name="三分块方案")
    third_scheme.config = {
        **SCHEME_CONFIG,
        "parse": {"engine": "mineru"},
        "chunk": {"mode": "page", "pages_per_chunk": 2},
    }
    await session.commit()
    await session.refresh(doc)
    doc.status = "done"
    await session.commit()
    rechunked = await client.post(
        f"/api/v1/documents/{doc.id}:reparse",
        json={"mode": "rechunk", "scheme_id": third_scheme.id},
    )
    assert rechunked.status_code == 200, rechunked.text
    assert rechunked.json()["step"] == 3
    await session.refresh(doc)
    assert doc.parse_snapshot["parse"]["engine"] == "markitdown"
    assert doc.parse_snapshot["chunk"]["mode"] == "page"


async def test_reparse_pipeline_starts_at_step(
    app, client: AsyncClient, session: AsyncSession, dispatch_calls, monkeypatch
) -> None:
    org = await make_org(session, "重新解析流水组织")
    owner = await make_user(session, Role.builder, org, username="doc_reparse_pipe")
    kb = await make_kb(session, org=org, user=owner)
    scheme = await make_scheme(session)
    doc = await make_doc(
        session, kb, owner.id, scheme_id=scheme.id, status="done", run_id="old-run"
    )
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc.id, "pdf"), b"original")
    login_as(client, owner)

    calls: list[str] = []

    async def fake_convert(*_args, **_kwargs):
        calls.append("convert")
        return "key", "pdf"

    async def fake_parse(*_args, **_kwargs):
        calls.append("parse")

    async def fake_chunk(*_args, **_kwargs):
        calls.append("chunk")

    async def fake_embed(*_args, **_kwargs):
        calls.append("embed")

    async def fake_activate(*_args, **_kwargs):
        calls.append("activate")

    monkeypatch_pipeline(
        monkeypatch,
        convert=fake_convert,
        parse=fake_parse,
        chunk=fake_chunk,
        embed=fake_embed,
        activate=fake_activate,
    )

    # 重新解析：从步骤 1 开始，全部重跑。
    reparsed = await client.post(f"/api/v1/documents/{doc.id}:reparse", json={"mode": "reparse"})
    assert reparsed.status_code == 200, reparsed.text
    await session.refresh(doc)
    assert doc.run_id != "old-run"
    await pipeline.run_pipeline(
        app.state.sessionmaker,
        app.state.redis,
        doc.id,
        doc.run_id,
        settings=app.state.settings,
        storage=app.state.storage,
    )
    assert calls == ["convert", "parse", "chunk", "embed", "activate"]

    # 仅重新分块：从步骤 3 开始，转换与解析不重跑。
    calls.clear()
    rechunked = await client.post(f"/api/v1/documents/{doc.id}:reparse", json={"mode": "rechunk"})
    assert rechunked.status_code == 200, rechunked.text
    await session.refresh(doc)
    await pipeline.run_pipeline(
        app.state.sessionmaker,
        app.state.redis,
        doc.id,
        doc.run_id,
        settings=app.state.settings,
        storage=app.state.storage,
    )
    assert calls == ["chunk", "embed", "activate"]


async def test_reparse_changes_run_id_cancels_running_task(
    app, client: AsyncClient, session: AsyncSession, dispatch_calls, monkeypatch
) -> None:
    """已经拿着旧 run_id 跑的任务，在重新解析换 run_id 后于步骤边界退出（8.3）。"""
    org = await make_org(session, "取消旧任务组织")
    owner = await make_user(session, Role.builder, org, username="doc_cancel_old")
    kb = await make_kb(session, org=org, user=owner)
    doc = await make_doc(session, kb, owner.id, status="done", run_id="old-run")
    # 任务开始前加载上下文（此时 run_id 还是 old-run），模拟“正在跑”。
    captured = await pipeline._load_context(app.state.sessionmaker, doc.id)
    assert captured is not None and captured.run_id == "old-run"

    async def fake_load(*_args, **_kwargs):
        return captured

    monkeypatch.setattr(pipeline, "_load_context", fake_load)
    login_as(client, owner)

    reparsed = await client.post(f"/api/v1/documents/{doc.id}:reparse", json={"mode": "reparse"})
    assert reparsed.status_code == 200, reparsed.text
    await session.refresh(doc)
    assert doc.run_id != "old-run"
    with pytest.raises(RunCancelled):
        await pipeline.run_pipeline(
            app.state.sessionmaker,
            app.state.redis,
            doc.id,
            "old-run",
            settings=app.state.settings,
            storage=app.state.storage,
        )


async def test_retry_from_embed_resumes_generation(
    app, client: AsyncClient, session: AsyncSession, dispatch_calls, monkeypatch
) -> None:
    """从步骤 5 继续时沿用上一次的分块代号，不重新分块。"""
    org = await make_org(session, "向量断点组织")
    owner = await make_user(session, Role.builder, org, username="doc_embed_resume")
    kb = await make_kb(session, org=org, user=owner)
    await create_index_table(session, kb.id, 1024)
    doc = await make_doc(
        session, kb, owner.id, status="failed", step=5, failed_step=5, chunk_count=1
    )
    session.add(
        Chunk(
            kb_id=kb.id,
            doc_id=doc.id,
            generation=3,
            seq=1,
            kind="normal",
            content="内容",
            source="auto",
        )
    )
    doc.current_generation = 2
    await session.commit()
    login_as(client, owner)
    await client.post(f"/api/v1/documents/{doc.id}:retry")
    await session.refresh(doc)

    seen: list[str] = []

    async def fake_convert(*_args, **_kwargs):
        seen.append("convert")
        return "key", "pdf"

    async def fake_parse(*_args, **_kwargs):
        seen.append("parse")

    async def fake_chunk(*_args, **_kwargs):
        seen.append("chunk")

    async def fake_embed(_sessionmaker, _redis, context):
        seen.append(f"embed:{context.generation}")

    async def fake_activate(*_args, **_kwargs):
        seen.append("activate")

    monkeypatch_pipeline(
        monkeypatch,
        convert=fake_convert,
        parse=fake_parse,
        chunk=fake_chunk,
        embed=fake_embed,
        activate=fake_activate,
    )
    await pipeline.run_pipeline(
        app.state.sessionmaker,
        app.state.redis,
        doc.id,
        doc.run_id,
        settings=app.state.settings,
        storage=app.state.storage,
    )
    assert seen == ["embed:3", "activate"]


# ---- 删除 ----


async def test_delete_cleans_rows_storage_index_and_writes_audit(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "删除文档组织")
    owner = await make_user(session, Role.builder, org, username="doc_delete_owner")
    kb = await make_kb(session, org=org, user=owner)
    await create_index_table(session, kb.id, 1024)
    doc = await make_doc(session, kb, owner.id, chunk_count=1)
    session.add(
        Chunk(
            kb_id=kb.id,
            doc_id=doc.id,
            generation=1,
            seq=1,
            kind="normal",
            content="内容",
            source="auto",
        )
    )
    await session.commit()
    await session.execute(
        text(
            f"INSERT INTO {index_table_name(kb.id)} "
            "(entry_type, chunk_id, doc_id, generation, active, embedding, body) "
            "VALUES ('chunk', 1, :doc_id, 1, true, :emb, '内容')"
        ),
        {"doc_id": doc.id, "emb": "[" + ",".join(["0.1"] * 1024) + "]"},
    )
    await session.commit()
    key = kb_original_key(kb.id, doc.id, "pdf")
    await app.state.storage.put_bytes(key, b"original")
    doc_id = doc.id
    kb_id = kb.id
    table = index_table_name(kb.id)
    login_as(client, owner)

    deleted = await client.delete(f"/api/v1/documents/{doc_id}")
    assert deleted.status_code == 204, deleted.text

    session.expire_all()
    assert await session.get(Document, doc_id) is None
    assert (
        await session.scalar(
            sa.select(sa.func.count()).select_from(Chunk).where(Chunk.doc_id == doc_id)
        )
        == 0
    )
    assert (
        await session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )
        == 0
    )
    assert not await app.state.storage.exists(key)
    audit = (
        await session.execute(
            sa.select(AuditLog).where(
                AuditLog.action == "document.delete", AuditLog.target_id == doc_id
            )
        )
    ).scalar_one()
    assert audit.target_type == "document"
    assert audit.result == "success"
    assert audit.detail["kb_id"] == kb_id


# ---- 批量操作 ----


async def test_batch_set_metadata_move_enable_disable(
    client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    org = await make_org(session, "批量组织")
    owner = await make_user(session, Role.builder, org, username="doc_batch_owner")
    kb = await make_kb(session, org=org, user=owner)
    await create_index_table(session, kb.id, 1024)
    folder = await make_folder(session, kb.id, "目标")
    field = KbMetadataField(kb_id=kb.id, name="部门", type="text")
    session.add(field)
    await session.commit()
    a = await make_doc(session, kb, owner.id, chunk_count=0)
    b = await make_doc(session, kb, owner.id, chunk_count=0)
    a.current_generation = 1
    await session.commit()
    await session.execute(
        text(
            f"INSERT INTO {index_table_name(kb.id)} "
            "(entry_type, chunk_id, doc_id, generation, active, embedding, body) "
            "VALUES ('chunk', 1, :doc_id, 1, false, :emb, 'x')"
        ),
        {"doc_id": a.id, "emb": "[" + ",".join(["0.1"] * 1024) + "]"},
    )
    await session.commit()
    login_as(client, owner)
    ids = [a.id, b.id]

    def batch(action: str, **extra):
        return client.post(
            "/api/v1/documents:batch", json={"action": action, "doc_ids": ids, **extra}
        )

    moved = await batch("move", folder_id=folder.id)
    assert moved.status_code == 200, moved.text
    assert set(moved.json()["succeeded"]) == set(ids)

    metadata = await batch("set_metadata", metadata={"部门": "研发", "多余": None})
    assert metadata.status_code == 422  # 未定义字段
    metadata = await batch("set_metadata", metadata={"部门": "研发"})
    assert metadata.status_code == 200, metadata.text
    await session.refresh(a)
    await session.refresh(b)
    assert a.doc_metadata == {"部门": "研发"}
    assert b.doc_metadata == {"部门": "研发"}

    disabled = await batch("disable")
    assert disabled.status_code == 200, disabled.text
    await session.refresh(a)
    assert a.enabled is False
    assert await _index_active_count(session, kb.id, a.id) == 0

    enabled = await batch("enable")
    assert enabled.status_code == 200, enabled.text
    await session.refresh(a)
    assert a.enabled is True

    await session.refresh(a)
    assert a.folder_id == folder.id
    # 移动到未分类。
    uncategorized = await batch("move", folder_id=None)
    assert uncategorized.status_code == 200, uncategorized.text
    await session.refresh(a)
    assert a.folder_id is None


async def test_batch_delete_audits_and_cross_kb_rejected(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "批量删除组织")
    owner = await make_user(session, Role.builder, org, username="doc_batch_del")
    kb = await make_kb(session, org=org, user=owner)
    other_kb = await make_kb(session, org=org, user=owner)
    a = await make_doc(session, kb, owner.id)
    b = await make_doc(session, kb, owner.id)
    outsider = await make_doc(session, other_kb, owner.id)
    a_id, b_id, outsider_id = a.id, b.id, outsider.id
    login_as(client, owner)

    cross = await client.post(
        "/api/v1/documents:batch",
        json={"action": "delete", "doc_ids": [a_id, outsider_id]},
    )
    assert cross.status_code == 422
    assert cross.json()["code"] == "VALIDATION_ERROR"

    deleted = await client.post(
        "/api/v1/documents:batch", json={"action": "delete", "doc_ids": [a_id, b_id]}
    )
    assert deleted.status_code == 200, deleted.text
    assert set(deleted.json()["succeeded"]) == {a_id, b_id}
    session.expire_all()
    assert await session.get(Document, a_id) is None
    audit = (
        await session.execute(sa.select(AuditLog).where(AuditLog.action == "document.batch_delete"))
    ).scalar_one()
    assert audit.target_type == "document"
    assert set(audit.detail["doc_ids"]) == {a_id, b_id}


async def test_batch_retry_and_reparse(
    client: AsyncClient, session: AsyncSession, dispatch_calls
) -> None:
    org = await make_org(session, "批量重试组织")
    owner = await make_user(session, Role.builder, org, username="doc_batch_retry")
    kb = await make_kb(session, org=org, user=owner)
    failed = await make_doc(session, kb, owner.id, status="failed", step=2, failed_step=2)
    done = await make_doc(session, kb, owner.id, status="done", step=6)
    login_as(client, owner)

    retry = await client.post(
        "/api/v1/documents:batch", json={"action": "retry", "doc_ids": [failed.id, done.id]}
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["succeeded"] == [failed.id]
    assert retry.json()["failed"][0]["doc_id"] == done.id
    await session.refresh(failed)
    assert failed.status == "queued" and failed.step == 2
    assert dispatch_calls

    reparse = await client.post(
        "/api/v1/documents:batch",
        json={"action": "reparse", "doc_ids": [done.id], "mode": "rechunk"},
    )
    assert reparse.status_code == 200, reparse.text
    await session.refresh(done)
    assert done.status == "queued" and done.step == 3


async def test_batch_set_metadata_validates_types(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "批量元数据类型组织")
    owner = await make_user(session, Role.builder, org, username="doc_batch_meta_type")
    kb = await make_kb(session, org=org, user=owner)
    session.add(KbMetadataField(kb_id=kb.id, name="页数", type="number"))
    await session.commit()
    doc = await make_doc(session, kb, owner.id)
    login_as(client, owner)

    bad = await client.post(
        "/api/v1/documents:batch",
        json={"action": "set_metadata", "doc_ids": [doc.id], "metadata": {"页数": "abc"}},
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "METADATA_VALUE_INVALID"

    good = await client.post(
        "/api/v1/documents:batch",
        json={"action": "set_metadata", "doc_ids": [doc.id], "metadata": {"页数": 12}},
    )
    assert good.status_code == 200, good.text
    await session.refresh(doc)
    assert doc.doc_metadata == {"页数": 12}


# ---- 启停与索引 active ----


async def test_disable_updates_index_active_and_hides_from_retrieval(
    app, client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    org = await make_org(session, "启停组织")
    owner = await make_user(session, Role.builder, org, username="doc_toggle_owner")
    kb = await make_kb(session, org=org, user=owner)
    await create_index_table(session, kb.id, 1024)
    doc = await make_doc(session, kb, owner.id, chunk_count=1)
    doc.current_generation = 1
    session.add(
        Chunk(
            kb_id=kb.id,
            doc_id=doc.id,
            generation=1,
            seq=1,
            kind="normal",
            content="长江大桥的报销标准",
            source="auto",
        )
    )
    await session.commit()
    await session.execute(
        text(
            f"INSERT INTO {index_table_name(kb.id)} "
            "(entry_type, chunk_id, doc_id, generation, active, embedding, body) "
            "VALUES ('chunk', 1, :doc_id, 1, true, :emb, '长江大桥的报销标准')"
        ),
        {"doc_id": doc.id, "emb": "[" + ",".join(["0.1"] * 1024) + "]"},
    )
    await session.commit()
    login_as(client, owner)

    config = RetrievalConfig(
        mode="vector", vector_weight=1, fulltext_weight=0, top_k=10, score_threshold=0
    )
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([0.1] * 1024))

    async def recall() -> list:
        result = await retrieval.recall_and_fuse(
            app.state.sessionmaker,
            app.state.redis,
            kb_ids=[kb.id],
            question="长江大桥",
            config=config,
        )
        return result.fusion

    disabled = await client.patch(f"/api/v1/documents/{doc.id}", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    assert await _index_active_count(session, kb.id, doc.id) == 0
    assert all(candidate.doc_id != doc.id for candidate in await recall())

    enabled = await client.patch(f"/api/v1/documents/{doc.id}", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert await _index_active_count(session, kb.id, doc.id) == 1
    assert any(candidate.doc_id == doc.id for candidate in await recall())
