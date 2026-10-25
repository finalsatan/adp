"""知识库业务表：同名唯一索引、默认方案外键限制、父子分块自引用（P3-03）。"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.kb.tables import (
    Chunk,
    Document,
    IngestScheme,
    KbDefaultScheme,
    KbFolder,
    KnowledgeBase,
)
from app.models.tables import Model
from app.resources.tables import Resource
from tests.factories import make_org, make_user


async def _make_kb(
    session: AsyncSession, *, org, user, name: str = "测试知识库", dim: int = 1024
) -> KnowledgeBase:
    resource = Resource(type="kb", name=name, org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    model = Model(
        name=f"向量模型-{name}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=dim,
        created_by=user.id,
    )
    session.add(model)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind="doc",
        embedding_model_id=model.id,
        embedding_dim=dim,
        index_table=f"kb_index.kb_{resource.id}",
    )
    session.add(kb)
    await session.flush()
    return kb


def _document(kb_id: int, user_id: int, folder_id: int | None, name: str) -> Document:
    return Document(
        kb_id=kb_id,
        folder_id=folder_id,
        name=name,
        file_type="pdf",
        ext="pdf",
        size=1024,
        sha256="a" * 64,
        status="queued",
        uploaded_by=user_id,
        run_by=user_id,
    )


async def test_document_name_unique_within_folder(session: AsyncSession) -> None:
    org = await make_org(session, "知识库组织")
    user = await make_user(session, role=Role.builder, org=org)
    kb = await _make_kb(session, org=org, user=user)
    # rollback 会让 ORM 对象过期，先取出普通值（否则再访问属性会触发同步 IO）。
    kb_id, user_id = kb.id, user.id

    folder_a = KbFolder(kb_id=kb_id, parent_id=None, path=f"/{kb_id}/1/", name="甲")
    folder_b = KbFolder(kb_id=kb_id, parent_id=None, path=f"/{kb_id}/2/", name="乙")
    session.add_all([folder_a, folder_b])
    await session.flush()
    folder_a_id, folder_b_id = folder_a.id, folder_b.id

    # 不同文件夹同名可以。
    session.add(_document(kb_id, user_id, folder_a_id, "说明.pdf"))
    session.add(_document(kb_id, user_id, folder_b_id, "说明.pdf"))
    await session.commit()

    # 同一文件夹内重名被唯一索引拒绝。
    session.add(_document(kb_id, user_id, folder_a_id, "说明.pdf"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    # folder_id 为空（未分类）也算同一文件夹：NULLS NOT DISTINCT。
    session.add(_document(kb_id, user_id, None, "报告.pdf"))
    await session.commit()
    session.add(_document(kb_id, user_id, None, "报告.pdf"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_default_scheme_delete_restricted(session: AsyncSession) -> None:
    org = await make_org(session, "知识库组织")
    user = await make_user(session, role=Role.builder, org=org)
    kb = await _make_kb(session, org=org, user=user)

    scheme = IngestScheme(
        builtin=False, file_type="pdf", name="PDF 通用方案", config={"schema_version": 1}
    )
    session.add(scheme)
    await session.flush()
    session.add(KbDefaultScheme(kb_id=kb.id, file_type="pdf", scheme_id=scheme.id))
    await session.commit()

    # 被设为默认的方案删不掉：外键 ON DELETE RESTRICT。
    await session.delete(scheme)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_chunk_parent_id_self_reference(session: AsyncSession) -> None:
    org = await make_org(session, "知识库组织")
    user = await make_user(session, role=Role.builder, org=org)
    kb = await _make_kb(session, org=org, user=user)
    doc = _document(kb.id, user.id, None, "制度.pdf")
    session.add(doc)
    await session.flush()

    parent = Chunk(
        kb_id=kb.id,
        doc_id=doc.id,
        generation=1,
        seq=0,
        kind="parent",
        content="父块内容",
        source="auto",
    )
    session.add(parent)
    await session.flush()
    child = Chunk(
        kb_id=kb.id,
        doc_id=doc.id,
        generation=1,
        seq=1,
        kind="child",
        parent_id=parent.id,
        content="子块内容",
        source="auto",
    )
    session.add(child)
    await session.commit()

    stored_parent_id = await session.scalar(select(Chunk.parent_id).where(Chunk.id == child.id))
    assert stored_parent_id == parent.id
