"""worker 测试包：建测试用知识库与文档的公共助手。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.kb.tables import Document, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from tests.factories import make_org, make_user


async def make_kb(
    session: AsyncSession, name: str = "调度测试库", dim: int = 1024
) -> tuple[KnowledgeBase, int]:
    """建一个文档库，返回（知识库, 上传人 id）。"""
    org = await make_org(session, f"{name}组织")
    user = await make_user(session, role=Role.builder, org=org)
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
    await session.commit()
    return kb, user.id


async def make_document(
    session: AsyncSession,
    kb_id: int,
    user_id: int,
    *,
    name: str = "文档.pdf",
    status: str = "queued",
    queued_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    started_at: datetime | None = None,
    run_id: str | None = "run-1",
) -> int:
    """建一个文档，返回 id。"""
    doc = Document(
        kb_id=kb_id,
        folder_id=None,
        name=name,
        file_type="pdf",
        ext="pdf",
        size=1024,
        sha256=uuid.uuid4().hex * 2,
        status=status,
        queued_at=queued_at or datetime.now(UTC),
        heartbeat_at=heartbeat_at,
        started_at=started_at,
        run_id=run_id,
        uploaded_by=user_id,
        run_by=user_id,
    )
    session.add(doc)
    await session.commit()
    return doc.id
