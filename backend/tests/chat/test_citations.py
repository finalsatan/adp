"""引用快照、角标与引用预览 / 下载测试（P4-10）。

连真实 PostgreSQL、Redis、RustFS；PDF 用 pypdfium2 生成的多页 PDF 验证“只返回被引用
页”，Word 用 P3-09 假解析服务的固定样例（`tests/fake_parser/fixtures/word`）验证上下文
片段与高亮。预览与下载走终端用户接口。
"""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

import pypdfium2 as pdfium
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import config_hash, default_config
from app.apps.tables import App, AppUsageTarget, AppVersion
from app.chat import citations
from app.chat.pipeline import AnswerRun
from app.chat.prompts import Reference
from app.chat.retrieval import AnswerCandidate
from app.chat.schemas import (
    CitationOut,
    CitationType,
)
from app.chat.tables import ChatAnswer, ChatTurn, Conversation
from app.core.schemas import Role
from app.core.storage import doc_prefix, kb_original_key, parsed_key
from app.iam.context import Ctx
from app.iam.permissions import ROLE_PERMISSIONS
from app.iam.tables import Org
from app.kb.schemas import FileType
from app.kb.tables import Document, DocumentParse, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource, ResourceMember
from tests.factories import auth_cookies, make_org, make_user

PREVIEW_URL = "/api/v1/answers/{answer_id}/citations/{n}/preview"
FILE_URL = "/api/v1/answers/{answer_id}/citations/{n}/file"
FIXTURES = Path(__file__).resolve().parents[1] / "fake_parser" / "fixtures"


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def ctx_of(session: AsyncSession, user) -> Ctx:
    org = await session.get(Org, user.org_id) if user.org_id is not None else None
    role = Role(user.role)
    return Ctx(
        user_id=user.id,
        username=user.username,
        name=user.name,
        role=role,
        status=user.status,
        org_id=org.id if org is not None else None,
        org_path=org.path if org is not None else None,
        org_name=org.name if org is not None else None,
        perms=ROLE_PERMISSIONS.get(role, frozenset()),
        session_id="test-session",
        pwc=False,
        ip=None,
        request_id=None,
    )


def make_pdf(pages: int) -> bytes:
    doc = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            doc.new_page(200, 200)
        buffer = io.BytesIO()
        doc.save(buffer)
        return buffer.getvalue()
    finally:
        doc.close()


async def make_kb(session: AsyncSession, org, owner, name: str) -> KnowledgeBase:
    resource = Resource(type="kb", name=name, org_id=org.id, owner_id=owner.id, visibility="org")
    session.add(resource)
    await session.flush()
    model = Model(
        name=f"向量模型-{name}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=1024,
        created_by=owner.id,
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


async def make_doc(
    session: AsyncSession,
    kb: KnowledgeBase,
    owner,
    *,
    name: str,
    file_type: str,
    ext: str,
    generation: int = 1,
) -> Document:
    doc = Document(
        kb_id=kb.id,
        folder_id=None,
        name=name,
        file_type=file_type,
        ext=ext,
        size=1,
        sha256=uuid.uuid4().hex * 2,
        status="done",
        current_generation=generation,
        uploaded_by=owner.id,
        run_by=owner.id,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc


async def make_app(
    session: AsyncSession,
    org,
    owner,
    *,
    allow_download: bool = False,
    usage_scope: str = "platform",
) -> App:
    config = default_config().model_dump(mode="json")
    config["reply"]["allow_download"] = allow_download
    resource = Resource(
        type="app",
        name=f"应用-{uuid.uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility="org",
    )
    session.add(resource)
    await session.flush()
    app = App(
        id=resource.id,
        app_type="kb_qa",
        draft_config=config,
        draft_revision=1,
        usage_scope=usage_scope,
    )
    session.add(app)
    version = AppVersion(
        app_id=resource.id,
        version_no=1,
        config=config,
        config_hash=config_hash(config),
        note="初版",
        published_by=owner.id,
    )
    session.add(version)
    await session.flush()
    app.published_version_id = version.id
    await session.commit()
    await session.refresh(app)
    return app


async def seed_answer(
    session: AsyncSession,
    app: App,
    user,
    citation_list: list[CitationOut],
    *,
    content: str = "回答[1]",
) -> ChatAnswer:
    conversation = Conversation(app_id=app.id, user_id=user.id, title="会话")
    session.add(conversation)
    await session.flush()
    turn = ChatTurn(conversation_id=conversation.id, seq=1, question="问题")
    session.add(turn)
    await session.flush()
    answer = ChatAnswer(
        turn_id=turn.id,
        seq=1,
        version_id=app.published_version_id,
        kind="generated",
        status="done",
        content=content,
        citations=[citation.model_dump(mode="json") for citation in citation_list],
        cited=[1],
    )
    session.add(answer)
    await session.flush()
    turn.current_answer_id = answer.id
    await session.commit()
    await session.refresh(answer)
    return answer


def doc_citation(doc: Document, **overrides) -> CitationOut:
    fields: dict = {
        "n": 1,
        "type": CitationType.doc,
        "kb_id": doc.kb_id,
        "kb_name": "知识库",
        "score": 0.9,
        "doc_id": doc.id,
        "doc_name": doc.name,
        "file_type": FileType(doc.file_type),
        "chunk_ids": [1],
        "generation": doc.current_generation,
        "content": "快照里的片段",
        "positions": [],
        "edited": False,
    }
    fields.update(overrides)
    return CitationOut(**fields)


async def seed_word_parse(app, session: AsyncSession, kb, doc, owner) -> None:
    doc_json = (FIXTURES / "word" / "doc.json").read_bytes()
    doc_md = (FIXTURES / "word" / "doc.md").read_bytes()
    await app.state.storage.put_bytes(parsed_key(kb.id, doc.id, "doc.json"), doc_json)
    await app.state.storage.put_bytes(parsed_key(kb.id, doc.id, "doc.md"), doc_md)
    session.add(
        DocumentParse(
            doc_id=doc.id,
            kind="original",
            doc_key=parsed_key(kb.id, doc.id, "doc.json"),
            md_key=parsed_key(kb.id, doc.id, "doc.md"),
            engine="docling",
            engine_version="1.10.0",
            pages=0,
            created_by=owner.id,
        )
    )
    await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _clean_objects(app):
    await app.state.storage.delete_prefix("kb/")
    yield
    await app.state.storage.delete_prefix("kb/")


# ---- 引用快照与角标 ----


def test_build_citations_snapshot_fields() -> None:
    candidate = AnswerCandidate(
        kb_id=101,
        kb_name="员工制度库",
        doc_id=3021,
        doc_name="员工考勤管理制度.docx",
        file_type=FileType.word,
        chunk_ids=[5512, 5513],
        generation=2,
        content="第十八条 新入职员工……",
        title_path="考勤",
        positions=[{"ref": "#/texts/6"}],
        edited=True,
        score=0.83,
    )
    run = AnswerRun(app_id=1, config=default_config(), question="q", sessionmaker=None, redis=None)
    run.references = [
        Reference(
            n=1,
            type="doc",
            source="员工考勤管理制度.docx",
            body="…",
            score=0.83,
            candidate=candidate,
        )
    ]

    result = citations.build_citations(run)

    assert len(result) == 1
    citation = result[0]
    assert citation.n == 1
    assert citation.type == CitationType.doc
    assert citation.kb_id == 101 and citation.kb_name == "员工制度库"
    assert citation.doc_id == 3021 and citation.doc_name == "员工考勤管理制度.docx"
    assert citation.file_type == FileType.word
    assert citation.chunk_ids == [5512, 5513]
    assert citation.generation == 2
    assert citation.content == "第十八条 新入职员工……"
    assert citation.positions == [{"ref": "#/texts/6"}]
    assert citation.edited is True
    assert citation.score == 0.83


def test_citations_for_event_drops_positions() -> None:
    citation = CitationOut(
        n=1,
        type=CitationType.doc,
        kb_id=1,
        kb_name="库",
        score=0.5,
        doc_id=2,
        doc_name="d.pdf",
        file_type=FileType.pdf,
        positions=[{"page": 1}],
    )
    event_citations = citations.citations_for_event([citation])
    assert event_citations[0].positions is None


def test_extract_cited_only_real_badges() -> None:
    assert citations.extract_cited("用了[1]和【3】，还有[1]") == [1, 3]
    assert citations.extract_cited("多条写在一个括号里[1、2]") == [1, 2]
    assert citations.extract_cited("没有角标") == []
    assert citations.extract_cited("") == []


# ---- PDF 预览 ----


async def test_pdf_preview_only_cited_pages_and_cache(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"PDF组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    kb = await make_kb(session, org, owner, "PDF库")
    doc = await make_doc(session, kb, owner, name="制度.pdf", file_type="pdf", ext="pdf")
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc.id, "pdf"), make_pdf(3))
    app_row = await make_app(session, org, owner)
    citation = doc_citation(
        doc,
        positions=[
            {"page": 1, "bbox": [0, 0, 1, 1], "origin": "BOTTOMLEFT"},
            {"page": 3, "bbox": [0, 0, 1, 1], "origin": "BOTTOMLEFT"},
        ],
    )
    answer = await seed_answer(session, app_row, user, [citation])
    login_as(client, user)

    response = await client.get(PREVIEW_URL.format(answer_id=answer.id, n=1))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["x-content-type-options"] == "nosniff"
    with pdfium.PdfDocument(response.content) as preview:
        assert len(preview) == 2

    # 缓存写入 kb/{kb}/docs/{doc}/pages/{gen}/{页码}.pdf。
    base = f"{doc_prefix(kb.id, doc.id)}pages/1"
    assert await app.state.storage.exists(f"{base}/1.pdf")
    assert await app.state.storage.exists(f"{base}/3.pdf")
    assert not await app.state.storage.exists(f"{base}/2.pdf")


async def test_pdf_preview_stale_uses_snapshot_page_numbers(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"PDF旧代-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    kb = await make_kb(session, org, owner, "旧代库")
    doc = await make_doc(
        session, kb, owner, name="制度.pdf", file_type="pdf", ext="pdf", generation=2
    )
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc.id, "pdf"), make_pdf(3))
    app_row = await make_app(session, org, owner)
    citation = doc_citation(doc, generation=1, positions=[{"page": 2}])
    answer = await seed_answer(session, app_row, user, [citation])
    login_as(client, user)

    response = await client.get(PREVIEW_URL.format(answer_id=answer.id, n=1))
    assert response.status_code == 200, response.text
    with pdfium.PdfDocument(response.content) as preview:
        assert len(preview) == 1
    # 缓存按快照代写入。
    assert await app.state.storage.exists(f"{doc_prefix(kb.id, doc.id)}pages/1/2.pdf")


# ---- Word 预览 ----


async def test_word_preview_context_and_highlight(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"Word组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    kb = await make_kb(session, org, owner, "Word库")
    doc = await make_doc(session, kb, owner, name="员工手册.docx", file_type="word", ext="docx")
    await seed_word_parse(app, session, kb, doc, owner)
    app_row = await make_app(session, org, owner)
    citation = doc_citation(
        doc,
        file_type=FileType.word,
        content="快照里的片段",
        positions=[{"ref": "#/texts/6"}],
    )
    answer = await seed_answer(session, app_row, user, [citation])
    login_as(client, user)

    response = await client.get(PREVIEW_URL.format(answer_id=answer.id, n=1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["render"] == "markdown"
    assert body["stale"] is False
    assert body["note"] is None
    assert [element["ref"] for element in body["elements"]] == [
        "#/texts/3",
        "#/texts/4",
        "#/texts/5",
        "#/texts/6",
        "#/texts/7",
        "#/texts/8",
        "#/tables/0",
    ]
    assert [element["ref"] for element in body["elements"] if element["highlight"]] == ["#/texts/6"]
    assert "工作时间是每个工作日九点到十八点" in body["content"]


async def test_word_preview_stale_returns_snapshot(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"Word旧代-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    kb = await make_kb(session, org, owner, "旧代Word库")
    doc = await make_doc(
        session, kb, owner, name="员工手册.docx", file_type="word", ext="docx", generation=2
    )
    await seed_word_parse(app, session, kb, doc, owner)
    app_row = await make_app(session, org, owner)
    citation = doc_citation(
        doc,
        file_type=FileType.word,
        generation=1,
        content="旧代快照片段",
        positions=[{"ref": "#/texts/6"}],
    )
    answer = await seed_answer(session, app_row, user, [citation])
    login_as(client, user)

    response = await client.get(PREVIEW_URL.format(answer_id=answer.id, n=1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["render"] == "snapshot"
    assert body["stale"] is True
    assert body["note"] == citations.STALE_NOTE
    assert body["content"] == "旧代快照片段"


async def test_edited_and_deleted_documents_use_snapshot(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"快照组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    kb = await make_kb(session, org, owner, "快照库")
    doc = await make_doc(session, kb, owner, name="员工手册.docx", file_type="word", ext="docx")
    await seed_word_parse(app, session, kb, doc, owner)
    app_row = await make_app(session, org, owner)
    login_as(client, user)

    edited = doc_citation(
        doc,
        file_type=FileType.word,
        content="人工修改后的快照",
        positions=[{"ref": "#/texts/6"}],
        edited=True,
    )
    edited_answer = await seed_answer(session, app_row, user, [edited])
    response = await client.get(PREVIEW_URL.format(answer_id=edited_answer.id, n=1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["render"] == "snapshot"
    assert body["note"] == citations.EDITED_NOTE
    assert body["content"] == "人工修改后的快照"

    # 文档已删除：只显示快照片段。
    deleted = doc_citation(
        doc,
        file_type=FileType.word,
        content="删除前的快照",
        positions=[{"ref": "#/texts/6"}],
    )
    deleted_answer = await seed_answer(session, app_row, user, [deleted])
    await session.delete(doc)
    await session.commit()
    deleted_response = await client.get(PREVIEW_URL.format(answer_id=deleted_answer.id, n=1))
    assert deleted_response.status_code == 200, deleted_response.text
    deleted_body = deleted_response.json()
    assert deleted_body["render"] == "snapshot"
    assert deleted_body["stale"] is True
    assert deleted_body["note"] == citations.STALE_NOTE
    assert deleted_body["content"] == "删除前的快照"


async def test_unsupported_type_returns_snapshot(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"Excel组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    kb = await make_kb(session, org, owner, "Excel库")
    doc = await make_doc(session, kb, owner, name="表格.xlsx", file_type="excel", ext="xlsx")
    app_row = await make_app(session, org, owner)
    citation = doc_citation(doc, file_type=FileType.excel, content="表格快照")
    answer = await seed_answer(session, app_row, user, [citation])
    login_as(client, user)

    response = await client.get(PREVIEW_URL.format(answer_id=answer.id, n=1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["render"] == "snapshot"
    assert body["note"] == citations.UNSUPPORTED_NOTE
    assert body["content"] == "表格快照"


# ---- 权限与错误码 ----


async def test_preview_citation_not_found_and_other_conversation(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"权限组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    other = await make_user(session, Role.end_user, org)
    kb = await make_kb(session, org, owner, "权限库")
    doc = await make_doc(session, kb, owner, name="制度.pdf", file_type="pdf", ext="pdf")
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc.id, "pdf"), make_pdf(1))
    app_row = await make_app(session, org, owner)
    citation = doc_citation(doc, positions=[{"page": 1}])
    answer = await seed_answer(session, app_row, user, [citation])

    login_as(client, user)
    missing = await client.get(PREVIEW_URL.format(answer_id=answer.id, n=99))
    assert missing.status_code == 404
    assert missing.json()["code"] == "CITATION_NOT_FOUND"

    login_as(client, other)
    forbidden = await client.get(PREVIEW_URL.format(answer_id=answer.id, n=1))
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "PERMISSION_DENIED"


async def test_preview_app_disabled_and_out_of_scope(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"停用组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    kb = await make_kb(session, org, owner, "停用库")
    doc = await make_doc(session, kb, owner, name="制度.pdf", file_type="pdf", ext="pdf")
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc.id, "pdf"), make_pdf(1))
    app_row = await make_app(session, org, owner)
    citation = doc_citation(doc, positions=[{"page": 1}])
    answer = await seed_answer(session, app_row, user, [citation])
    login_as(client, user)

    resource = await session.get(Resource, app_row.id)
    assert resource is not None
    resource.status = "disabled"
    await session.commit()
    disabled = await client.get(PREVIEW_URL.format(answer_id=answer.id, n=1))
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "APP_DISABLED"

    resource.status = "enabled"
    app_row.usage_scope = "users"
    session.add(AppUsageTarget(app_id=app_row.id, target_type="user", target_id=owner.id))
    await session.commit()
    out_of_scope = await client.get(PREVIEW_URL.format(answer_id=answer.id, n=1))
    assert out_of_scope.status_code == 403
    assert out_of_scope.json()["code"] == "APP_NOT_IN_SCOPE"


# ---- 下载 ----


async def test_download_uses_current_published_version(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"下载组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    kb = await make_kb(session, org, owner, "下载库")
    doc = await make_doc(session, kb, owner, name="差旅报销制度.pdf", file_type="pdf", ext="pdf")
    content = make_pdf(1)
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc.id, "pdf"), content)
    app_row = await make_app(session, org, owner, allow_download=False)
    citation = doc_citation(doc, positions=[{"page": 1}])
    answer = await seed_answer(session, app_row, user, [citation])
    login_as(client, user)

    # 生成回答时的版本未开启下载。
    blocked = await client.get(FILE_URL.format(answer_id=answer.id, n=1))
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "DOWNLOAD_NOT_ALLOWED"

    # 当前发布版本开启下载后立即可以下载；回答仍指向旧版本。
    version = await session.get(AppVersion, app_row.published_version_id)
    assert version is not None
    config = dict(version.config)
    config["reply"] = {**config["reply"], "allow_download": True}
    version.config = config
    await session.commit()

    allowed = await client.get(FILE_URL.format(answer_id=answer.id, n=1))
    assert allowed.status_code == 200, allowed.text
    assert allowed.content == content
    assert allowed.headers["content-disposition"].startswith("inline;")
    assert allowed.headers["x-content-type-options"] == "nosniff"

    attachment = await client.get(FILE_URL.format(answer_id=answer.id, n=1), params={"download": 1})
    assert attachment.headers["content-disposition"].startswith("attachment;")

    ranged = await client.get(
        FILE_URL.format(answer_id=answer.id, n=1), headers={"Range": "bytes=0-9"}
    )
    assert ranged.status_code == 206
    assert ranged.content == content[:10]

    # 发布新版本关闭下载后立即生效，不看生成回答时的版本。
    new_config = dict(config)
    new_config["reply"] = {**new_config["reply"], "allow_download": False}
    new_version = AppVersion(
        app_id=app_row.id,
        version_no=2,
        config=new_config,
        config_hash=config_hash(new_config),
        note="关闭下载",
        published_by=owner.id,
    )
    session.add(new_version)
    await session.flush()
    app_row.published_version_id = new_version.id
    await session.commit()
    disabled = await client.get(FILE_URL.format(answer_id=answer.id, n=1))
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "DOWNLOAD_NOT_ALLOWED"


# ---- 搭建者：有知识库查看权限时返回整份 ----


async def test_collaborator_whole_document_by_kb_permission(app, session: AsyncSession) -> None:
    group = await make_org(session, f"整份集团-{uuid.uuid4().hex[:6]}")
    rd = await make_org(session, "整份研发", parent=group)
    product = await make_org(session, "整份产品", parent=group)
    owner = await make_user(session, Role.builder, rd)
    collab = await make_user(session, Role.builder, product)
    outsider = await make_user(session, Role.builder, product)
    kb = await make_kb(session, rd, owner, "整份库")
    doc = await make_doc(session, kb, owner, name="员工手册.docx", file_type="word", ext="docx")
    await seed_word_parse(app, session, kb, doc, owner)
    session.add(ResourceMember(resource_id=kb.id, user_id=collab.id, added_by=owner.id))
    await session.commit()
    citation = doc_citation(
        doc,
        file_type=FileType.word,
        content="快照片段",
        positions=[{"ref": "#/texts/6"}],
    )

    collab_ctx = await ctx_of(session, collab)
    assert await citations.can_view_whole(session, collab_ctx, kb.id) is True
    whole_response = await citations.render_preview(
        session, app.state.storage, citation, whole_document=True
    )
    whole = json.loads(whole_response.body)
    assert whole["render"] == "markdown"
    assert whole["elements"] is not None
    assert len(whole["elements"]) == 14
    assert whole["content"] == (FIXTURES / "word" / "doc.md").read_text(encoding="utf-8")

    outsider_ctx = await ctx_of(session, outsider)
    assert await citations.can_view_whole(session, outsider_ctx, kb.id) is False
    partial_response = await citations.render_preview(
        session, app.state.storage, citation, whole_document=False
    )
    partial = json.loads(partial_response.body)
    assert partial["elements"] is not None
    assert [element["ref"] for element in partial["elements"]] == [
        "#/texts/3",
        "#/texts/4",
        "#/texts/5",
        "#/texts/6",
        "#/texts/7",
        "#/texts/8",
        "#/tables/0",
    ]
