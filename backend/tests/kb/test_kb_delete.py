"""知识库删除：索引表、对象存储前缀、资源软删除与重建（P3-04）。"""

import uuid

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.kb.index_table import index_table_exists
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_embedding_model(session: AsyncSession, owner) -> Model:
    model = Model(
        name=f"删除模型-{uuid.uuid4().hex[:8]}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=1024,
        embedding_max_input=8192,
        created_by=owner.id,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def test_delete_kb_cleans_index_table_storage_and_resource(
    client: AsyncClient, session: AsyncSession, app
) -> None:
    org = await make_org(session, "删除知识库组织")
    owner = await make_user(session, Role.builder, org, username="delete_kb_owner")
    model = await make_embedding_model(session, owner)
    login_as(client, owner)

    created = await client.post(
        "/api/v1/kbs",
        json={
            "kind": "doc",
            "name": "待删除库",
            "embedding_model_id": model.id,
            "visibility": "org",
        },
    )
    assert created.status_code == 201, created.text
    kb_id = created.json()["id"]

    assert await index_table_exists(session, kb_id)

    storage = app.state.storage
    key = f"kb/{kb_id}/docs/1/original.pdf"
    await storage.put_bytes(key, b"data")
    assert await storage.exists(key)

    deleted = await client.delete(f"/api/v1/kbs/{kb_id}")
    assert deleted.status_code == 204, deleted.text

    # 索引表删掉、对象前缀清空、knowledge_bases 行硬删、resources 软删。
    assert not await index_table_exists(session, kb_id)
    assert not await storage.exists(key)
    assert (
        await session.scalar(sa.select(KnowledgeBase.id).where(KnowledgeBase.id == kb_id)) is None
    )
    resource = await session.get(Resource, kb_id)
    assert resource is not None and resource.deleted_at is not None

    # 同名可以再建（软删除的资源不占名字）。
    again = await client.post(
        "/api/v1/kbs",
        json={
            "kind": "doc",
            "name": "待删除库",
            "embedding_model_id": model.id,
            "visibility": "org",
        },
    )
    assert again.status_code == 201, again.text
    assert again.json()["id"] != kb_id


async def test_delete_kb_storage_failure_does_not_fail(
    client: AsyncClient, session: AsyncSession, app, monkeypatch
) -> None:
    org = await make_org(session, "存储失败组织")
    owner = await make_user(session, Role.builder, org, username="storage_fail_owner")
    model = await make_embedding_model(session, owner)
    login_as(client, owner)

    created = await client.post(
        "/api/v1/kbs",
        json={
            "kind": "doc",
            "name": "存储失败库",
            "embedding_model_id": model.id,
            "visibility": "org",
        },
    )
    kb_id = created.json()["id"]

    async def boom(prefix: str) -> int:
        raise RuntimeError("对象存储不可用")

    monkeypatch.setattr(app.state.storage, "delete_prefix", boom)

    # 前缀删除失败只记日志，接口仍成功，索引表与业务行照常清理。
    deleted = await client.delete(f"/api/v1/kbs/{kb_id}")
    assert deleted.status_code == 204, deleted.text
    assert not await index_table_exists(session, kb_id)
    assert (
        await session.scalar(sa.select(KnowledgeBase.id).where(KnowledgeBase.id == kb_id)) is None
    )
