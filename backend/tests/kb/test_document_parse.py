"""解析结果与引用位置数据（P3-14，知识库技术方案 7、11.1、11.2、13）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.core.storage import parsed_key
from app.kb.tables import Document, DocumentParse, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user

PARSE_URL = "/api/v1/documents/{doc_id}/parse"

# 一个最小的 DoclingDocument：body 的阅读顺序为 标题 → 表格 → 图片 → 正文。
DOCLING_DOCUMENT = {
    "schema_name": "DoclingDocument",
    "version": "1.10.0",
    "name": "差旅报销制度",
    "body": {
        "self_ref": "#/body",
        "children": [
            {"$ref": "#/texts/0"},
            {"$ref": "#/tables/0"},
            {"$ref": "#/pictures/0"},
            {"$ref": "#/texts/1"},
        ],
    },
    "groups": [],
    "texts": [
        {"self_ref": "#/texts/0", "label": "section_header", "text": "第一章 总则"},
        {"self_ref": "#/texts/1", "label": "text", "text": "本制度适用于公司全体员工。"},
        {"self_ref": "#/texts/2", "label": "caption", "text": "图 1 报销流程"},
    ],
    "tables": [
        {
            "self_ref": "#/tables/0",
            "label": "table",
            "data": {
                "table_cells": [
                    {"start_row_offset_idx": 0, "start_col_offset_idx": 0, "text": "项目"},
                    {"start_row_offset_idx": 0, "start_col_offset_idx": 1, "text": "标准"},
                    {"start_row_offset_idx": 1, "start_col_offset_idx": 0, "text": "交通"},
                    {"start_row_offset_idx": 1, "start_col_offset_idx": 1, "text": "高铁二等座"},
                ]
            },
        }
    ],
    "pictures": [
        {
            "self_ref": "#/pictures/0",
            "label": "picture",
            "captions": [{"$ref": "#/texts/2"}],
        }
    ],
}
MARKDOWN = "# 差旅报销制度\n\n本制度适用于公司全体员工。\n"


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_kb(session: AsyncSession, *, org, user, name: str = "解析知识库") -> KnowledgeBase:
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
    session: AsyncSession, kb: KnowledgeBase, user, *, status: str = "done"
) -> Document:
    doc = Document(
        kb_id=kb.id,
        folder_id=None,
        name="差旅报销制度.pdf",
        file_type="pdf",
        ext="pdf",
        size=128,
        sha256="b" * 64,
        status=status,
        queued_at=datetime.now(UTC),
        uploaded_by=user.id,
        run_by=user.id,
    )
    session.add(doc)
    await session.commit()
    return doc


async def seed_parse(
    app,
    session: AsyncSession,
    kb: KnowledgeBase,
    doc: Document,
    user,
    *,
    kind: str = "original",
    engine: str = "docling",
) -> None:
    await app.state.storage.put_bytes(
        parsed_key(kb.id, doc.id, "doc.json", edited=kind == "edited"),
        json.dumps(DOCLING_DOCUMENT).encode(),
    )
    await app.state.storage.put_bytes(
        parsed_key(kb.id, doc.id, "doc.md", edited=kind == "edited"),
        MARKDOWN.encode(),
    )
    session.add(
        DocumentParse(
            doc_id=doc.id,
            kind=kind,
            doc_key=parsed_key(kb.id, doc.id, "doc.json", edited=kind == "edited"),
            md_key=parsed_key(kb.id, doc.id, "doc.md", edited=kind == "edited"),
            engine=engine,
            engine_version="1.10.0",
            pages=2,
            ocr_pages=1,
            created_by=user.id,
        )
    )
    await session.commit()


async def test_parse_original_returns_markdown_and_elements(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "解析组织")
    owner = await make_user(session, Role.builder, org, username="parse_owner")
    kb = await make_kb(session, org=org, user=owner)
    doc = await make_doc(session, kb, owner)
    await seed_parse(app, session, kb, doc, owner)
    login_as(client, owner)

    response = await client.get(PARSE_URL.format(doc_id=doc.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "original"
    assert body["markdown"] == MARKDOWN
    assert body["engine"] == "docling"
    assert body["engine_version"] == "1.10.0"
    assert body["pages"] == 2
    assert body["ocr_pages"] == 1
    assert body["edited_by"] is None
    assert body["edited_at"] is None

    assert [element["ref"] for element in body["elements"]] == [
        "#/texts/0",
        "#/tables/0",
        "#/pictures/0",
        "#/texts/1",
    ]
    assert [element["type"] for element in body["elements"]] == [
        "section_header",
        "table",
        "picture",
        "text",
    ]
    assert body["elements"][0]["text"] == "第一章 总则"
    assert body["elements"][1]["text"] == "项目 | 标准\n交通 | 高铁二等座"
    assert body["elements"][2]["text"] == "图 1 报销流程"
    assert body["elements"][3]["text"] == "本制度适用于公司全体员工。"


async def test_parse_edited_returns_edit_info(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "修改版组织")
    owner = await make_user(session, Role.builder, org, username="edited_owner")
    kb = await make_kb(session, org=org, user=owner)
    doc = await make_doc(session, kb, owner)
    await seed_parse(app, session, kb, doc, owner, kind="original")
    await seed_parse(app, session, kb, doc, owner, kind="edited")
    login_as(client, owner)

    response = await client.get(PARSE_URL.format(doc_id=doc.id), params={"kind": "edited"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "edited"
    assert body["edited_by"]["id"] == owner.id
    assert body["edited_at"] is not None


async def test_parse_edited_missing_returns_404(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "无修改版组织")
    owner = await make_user(session, Role.builder, org, username="no_edited_owner")
    kb = await make_kb(session, org=org, user=owner)
    doc = await make_doc(session, kb, owner)
    await seed_parse(app, session, kb, doc, owner, kind="original")
    login_as(client, owner)

    response = await client.get(PARSE_URL.format(doc_id=doc.id), params={"kind": "edited"})
    assert response.status_code == 404
    assert "人工修改" in response.json()["message"]


async def test_parse_not_ready_and_permissions(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    group = await make_org(session, "解析权限集团")
    rd = await make_org(session, "解析权限研发", parent=group)
    product = await make_org(session, "解析权限产品", parent=group)
    owner = await make_user(session, Role.builder, rd, username="parse_perm_owner")
    outsider = await make_user(session, Role.builder, product, username="parse_perm_out")
    kb = await make_kb(session, org=rd, user=owner)
    doc = await make_doc(session, kb, owner, status="queued")
    login_as(client, owner)

    # 处理中、还没有解析结果 → 404。
    not_ready = await client.get(PARSE_URL.format(doc_id=doc.id))
    assert not_ready.status_code == 404

    login_as(client, outsider)
    hidden = await client.get(PARSE_URL.format(doc_id=doc.id))
    assert hidden.status_code == 404
