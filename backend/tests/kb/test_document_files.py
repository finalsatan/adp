"""文档原文件、预览 PDF 与图片读取（P3-14，知识库技术方案 11.1、13）。"""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.core.storage import doc_file_key, images_prefix, kb_original_key
from app.kb.tables import Document, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource, ResourceMember
from tests.factories import auth_cookies, make_org, make_user

FILE_URL = "/api/v1/documents/{doc_id}/file"
IMAGE_URL = "/api/v1/documents/{doc_id}/images/{name}"


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_kb(session: AsyncSession, *, org, user, name: str = "文件知识库") -> KnowledgeBase:
    resource = Resource(type="kb", name=name, org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    model = Model(
        name=f"向量模型-{name}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=1024,
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


async def make_doc(
    session: AsyncSession,
    kb: KnowledgeBase,
    user,
    *,
    name: str = "文档.pdf",
    file_type: str = "pdf",
    ext: str = "pdf",
    size: int = 0,
    status: str = "done",
) -> Document:
    doc = Document(
        kb_id=kb.id,
        folder_id=None,
        name=name,
        file_type=file_type,
        ext=ext,
        size=size,
        sha256="a" * 64,
        status=status,
        queued_at=datetime.now(UTC),
        uploaded_by=user.id,
        run_by=user.id,
    )
    session.add(doc)
    await session.commit()
    return doc


async def test_read_original_permissions(app, client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "文件权限集团")
    rd = await make_org(session, "文件权限研发", parent=group)
    product = await make_org(session, "文件权限产品", parent=group)
    owner = await make_user(session, Role.builder, rd, username="file_owner")
    viewer = await make_user(session, Role.builder, rd, username="file_viewer")
    collab = await make_user(session, Role.builder, product, username="file_collab")
    outsider = await make_user(session, Role.builder, product, username="file_outsider")
    kb = await make_kb(session, org=rd, user=owner)
    content = b"original file bytes"
    doc = await make_doc(session, kb, owner, size=len(content))
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc.id, "pdf"), content)
    session.add(ResourceMember(resource_id=kb.id, user_id=collab.id, added_by=owner.id))
    await session.commit()

    login_as(client, owner)
    response = await client.get(FILE_URL.format(doc_id=doc.id))
    assert response.status_code == 200, response.text
    assert response.content == content
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["accept-ranges"] == "bytes"

    login_as(client, viewer)
    allowed = await client.get(FILE_URL.format(doc_id=doc.id))
    assert allowed.status_code == 200, allowed.text

    login_as(client, collab)
    as_member = await client.get(FILE_URL.format(doc_id=doc.id))
    assert as_member.status_code == 200, as_member.text

    login_as(client, outsider)
    hidden = await client.get(FILE_URL.format(doc_id=doc.id))
    assert hidden.status_code == 404


async def test_range_requests(app, client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "Range 组织")
    owner = await make_user(session, Role.builder, org, username="range_owner")
    kb = await make_kb(session, org=org, user=owner)
    content = b"0123456789" * 20  # 200 字节
    doc = await make_doc(session, kb, owner, size=len(content))
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc.id, "pdf"), content)
    login_as(client, owner)

    ranged = await client.get(FILE_URL.format(doc_id=doc.id), headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206, ranged.text
    assert ranged.content == content[:100]
    assert ranged.headers["content-range"] == f"bytes 0-99/{len(content)}"
    assert ranged.headers["content-length"] == "100"
    assert ranged.headers["accept-ranges"] == "bytes"

    full = await client.get(FILE_URL.format(doc_id=doc.id))
    assert full.status_code == 200
    assert full.content == content
    assert "content-range" not in full.headers

    out_of_range = await client.get(
        FILE_URL.format(doc_id=doc.id), headers={"Range": "bytes=1000-1099"}
    )
    assert out_of_range.status_code == 416, out_of_range.text
    assert out_of_range.json()["code"] == "RANGE_NOT_SATISFIABLE"

    # 多段 Range 按 200 全量返回。
    multi = await client.get(FILE_URL.format(doc_id=doc.id), headers={"Range": "bytes=0-9,20-29"})
    assert multi.status_code == 200
    assert multi.content == content


async def test_preview_missing_and_present(app, client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "预览组织")
    owner = await make_user(session, Role.builder, org, username="preview_owner")
    kb = await make_kb(session, org=org, user=owner)
    doc = await make_doc(session, kb, owner, name="报销制度.docx", file_type="word", ext="docx")
    login_as(client, owner)

    missing = await client.get(FILE_URL.format(doc_id=doc.id), params={"variant": "preview"})
    assert missing.status_code == 404, missing.text
    assert "预览" in missing.json()["message"]

    preview = b"%PDF-1.4 preview bytes"
    await app.state.storage.put_bytes(doc_file_key(kb.id, doc.id, "preview.pdf"), preview)
    present = await client.get(FILE_URL.format(doc_id=doc.id), params={"variant": "preview"})
    assert present.status_code == 200, present.text
    assert present.content == preview
    assert present.headers["content-type"].startswith("application/pdf")


async def test_download_disposition(app, client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "下载组织")
    owner = await make_user(session, Role.builder, org, username="download_owner")
    kb = await make_kb(session, org=org, user=owner)
    doc = await make_doc(session, kb, owner, name="差旅报销制度.pdf")
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc.id, "pdf"), b"data")
    login_as(client, owner)

    inline = await client.get(FILE_URL.format(doc_id=doc.id))
    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline;")

    download = await client.get(FILE_URL.format(doc_id=doc.id), params={"download": 1})
    assert download.status_code == 200
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    # 文件名用 RFC 5987 编码，中文不直接出现。
    assert "filename*=UTF-8''" in disposition


async def test_image_read_and_name_validation(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "图片组织")
    owner = await make_user(session, Role.builder, org, username="image_owner")
    kb = await make_kb(session, org=org, user=owner)
    doc = await make_doc(session, kb, owner)
    png = b"\x89PNG\r\n\x1a\nfake"
    await app.state.storage.put_bytes(f"{images_prefix(kb.id, doc.id)}1.png", png)
    login_as(client, owner)

    response = await client.get(IMAGE_URL.format(doc_id=doc.id, name="1"))
    assert response.status_code == 200, response.text
    assert response.content == png
    assert response.headers["content-type"].startswith("image/png")

    traversal = await client.get(IMAGE_URL.format(doc_id=doc.id, name="a..b"))
    assert traversal.status_code == 404

    # 含 `/` 的名字不会匹配路由，同样 404。
    slash = await client.get(f"/api/v1/documents/{doc.id}/images/a/b")
    assert slash.status_code == 404

    missing = await client.get(IMAGE_URL.format(doc_id=doc.id, name="nope"))
    assert missing.status_code == 404


def _assert_security_headers(response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"


async def test_inline_whitelist_and_security_headers(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    """HTML 等类型强制下载（防存储型 XSS），纯文本按 text/plain；文件与图片都带安全头。"""
    org = await make_org(session, "安全头组织")
    owner = await make_user(session, Role.builder, org, username="security_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    # HTML 原文件：默认与 download=1 都强制 attachment。
    html_doc = await make_doc(session, kb, owner, name="evil.html", file_type="html", ext="html")
    await app.state.storage.put_bytes(
        kb_original_key(kb.id, html_doc.id, "html"), b"<script>alert(1)</script>"
    )
    html = await client.get(FILE_URL.format(doc_id=html_doc.id))
    assert html.status_code == 200, html.text
    assert html.headers["content-disposition"].startswith("attachment;")
    _assert_security_headers(html)

    forced = await client.get(FILE_URL.format(doc_id=html_doc.id), params={"download": 1})
    assert forced.headers["content-disposition"].startswith("attachment;")
    _assert_security_headers(forced)

    # 纯文本：text/plain; charset=utf-8，允许 inline。
    txt_doc = await make_doc(session, kb, owner, name="note.txt", file_type="txt", ext="txt")
    await app.state.storage.put_bytes(kb_original_key(kb.id, txt_doc.id, "txt"), "你好".encode())
    txt = await client.get(FILE_URL.format(doc_id=txt_doc.id))
    assert txt.status_code == 200, txt.text
    assert txt.headers["content-type"] == "text/plain; charset=utf-8"
    assert txt.headers["content-disposition"].startswith("inline;")
    _assert_security_headers(txt)

    # PDF inline 也带安全头。
    pdf_doc = await make_doc(session, kb, owner, name="doc.pdf", file_type="pdf", ext="pdf")
    await app.state.storage.put_bytes(kb_original_key(kb.id, pdf_doc.id, "pdf"), b"%PDF-1.4")
    pdf = await client.get(FILE_URL.format(doc_id=pdf_doc.id))
    assert pdf.headers["content-disposition"].startswith("inline;")
    _assert_security_headers(pdf)

    # 图片接口也带安全头。
    png = b"\x89PNG\r\n\x1a\nsecure"
    await app.state.storage.put_bytes(f"{images_prefix(kb.id, pdf_doc.id)}9.png", png)
    image = await client.get(IMAGE_URL.format(doc_id=pdf_doc.id, name="9"))
    assert image.status_code == 200, image.text
    _assert_security_headers(image)
