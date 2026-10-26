"""草稿接口测试（P4-04）：保存、乐观锁、格式校验、重排要求与 `has_changes`。"""

import asyncio
import uuid

import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.tables import App
from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_chat_model(session: AsyncSession, owner, *, status: str = "enabled") -> Model:
    model = Model(
        name=f"对话模型-{uuid.uuid4().hex[:8]}",
        model_type="chat",
        litellm_model="openai/gpt-4o-mini",
        status=status,
        created_by=owner.id,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def make_embedding_model(
    session: AsyncSession, owner, *, dim: int = 1024, status: str = "enabled"
) -> Model:
    model = Model(
        name=f"向量模型-{uuid.uuid4().hex[:8]}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=dim,
        embedding_max_input=8192,
        status=status,
        created_by=owner.id,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def make_kb(
    session: AsyncSession,
    org,
    owner,
    embedding_model: Model,
    *,
    kind: str = "doc",
    visibility: str = "org",
    name: str | None = None,
) -> KnowledgeBase:
    resource = Resource(
        type="kb",
        name=name or f"知识库-{uuid.uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility=visibility,
    )
    session.add(resource)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind=kind,
        embedding_model_id=embedding_model.id,
        embedding_dim=embedding_model.embedding_dim or 1024,
        index_table=f"kb_index.kb_{resource.id}",
    )
    session.add(kb)
    await session.commit()
    await session.refresh(kb)
    return kb


async def create_app(client: AsyncClient, *, name: str | None = None, visibility: str = "org"):
    return await client.post(
        "/api/v1/apps",
        json={
            "app_type": "kb_qa",
            "name": name or f"应用-{uuid.uuid4().hex[:8]}",
            "visibility": visibility,
        },
    )


def draft_config(
    *,
    chat_model_id: int | None = None,
    kb_ids: tuple[int, ...] = (),
    doc_rerank: bool = False,
) -> dict:
    return {
        "app_type": "kb_qa",
        "model": {"chat_model_id": chat_model_id} if chat_model_id is not None else None,
        "knowledge": {
            "kbs": [{"kb_id": kb_id} for kb_id in kb_ids],
            "doc": {"rerank": {"enabled": doc_rerank}},
        },
    }


async def put_draft(client: AsyncClient, app_id: int, revision: int, config: dict):
    return await client.put(
        f"/api/v1/apps/{app_id}/draft",
        json={"draft_revision": revision, "config": config},
    )


async def test_save_and_get_draft(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "草稿组织")
    builder = await make_user(session, Role.builder, org, username="draft_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()
    chat_model = await make_chat_model(session, builder)
    embedding = await make_embedding_model(session, builder)
    kb = await make_kb(session, org, builder, embedding)

    initial = await client.get(f"/api/v1/apps/{app['id']}/draft")
    assert initial.status_code == 200
    body = initial.json()
    assert body["draft_revision"] == 1
    assert body["has_changes"] is True
    assert body["updated_by"]["id"] == builder.id
    assert body["updated_at"] is not None

    saved = await put_draft(
        client, app["id"], 1, draft_config(chat_model_id=chat_model.id, kb_ids=(kb.id,))
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft_revision"] == 2
    assert saved.json()["has_changes"] is True
    assert saved.json()["config"]["model"]["chat_model_id"] == chat_model.id
    assert [ref["kb_id"] for ref in saved.json()["config"]["knowledge"]["kbs"]] == [kb.id]

    row = await session.get(App, app["id"])
    assert row is not None
    assert row.draft_revision == 2
    assert row.draft_config["model"]["chat_model_id"] == chat_model.id
    assert row.draft_updated_by == builder.id

    # 保存草稿不写审计（应用技术方案 14）。
    actions = (
        (
            await session.execute(
                sa.select(AuditLog.action).where(
                    AuditLog.target_type == "app", AuditLog.target_id == app["id"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert "app.create" in actions
    assert "app.publish" not in actions and "app.restore" not in actions


async def test_draft_conflict(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "草稿冲突组织")
    owner = await make_user(session, Role.builder, org, username="draft_conflict_owner")
    member = await make_user(session, Role.builder, org, username="draft_conflict_member")
    login_as(client, owner)
    app = (await create_app(client)).json()
    added = await client.post(f"/api/v1/apps/{app['id']}/members", json={"user_id": member.id})
    assert added.status_code == 201

    login_as(client, owner)
    first = await put_draft(client, app["id"], 1, draft_config())
    assert first.status_code == 200
    assert first.json()["draft_revision"] == 2

    # 另一个协作者还拿着 revision 1。
    login_as(client, member)
    stale = await put_draft(client, app["id"], 1, draft_config())
    assert stale.status_code == 409
    assert stale.json()["code"] == "DRAFT_CONFLICT"

    fresh = await put_draft(client, app["id"], 2, draft_config())
    assert fresh.status_code == 200
    assert fresh.json()["draft_revision"] == 3


async def test_save_incomplete_config_allowed(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "不完整配置组织")
    builder = await make_user(session, Role.builder, org, username="draft_incomplete_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()

    # 没选模型、没选知识库也能保存。
    saved = await put_draft(client, app["id"], 1, {"app_type": "kb_qa"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft_revision"] == 2
    assert saved.json()["config"]["model"] is None
    assert saved.json()["config"]["knowledge"]["kbs"] == []


async def test_save_draft_rerank_required(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "重排组织")
    builder = await make_user(session, Role.builder, org, username="draft_rerank_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()
    embedding_a = await make_embedding_model(session, builder, dim=1024)
    embedding_b = await make_embedding_model(session, builder, dim=768)
    kb_a = await make_kb(session, org, builder, embedding_a, name="重排库A")
    kb_b = await make_kb(session, org, builder, embedding_b, name="重排库B")

    blocked = await put_draft(
        client, app["id"], 1, draft_config(kb_ids=(kb_a.id, kb_b.id), doc_rerank=False)
    )
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "VALIDATION_ERROR"
    problems = blocked.json()["detail"]["problems"]
    assert any(problem["code"] == "DOC_RERANK_REQUIRED" for problem in problems)

    allowed = await put_draft(
        client, app["id"], 1, draft_config(kb_ids=(kb_a.id, kb_b.id), doc_rerank=True)
    )
    assert allowed.status_code == 200, allowed.text


async def test_has_changes_across_publish(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "变更组织")
    builder = await make_user(session, Role.builder, org, username="draft_changes_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()
    chat_model = await make_chat_model(session, builder)
    embedding = await make_embedding_model(session, builder)
    kb = await make_kb(session, org, builder, embedding)
    config = draft_config(chat_model_id=chat_model.id, kb_ids=(kb.id,))
    assert (await put_draft(client, app["id"], 1, config)).status_code == 200

    published = await client.post(f"/api/v1/apps/{app['id']}:publish", json={"note": "初版"})
    assert published.status_code == 200, published.text
    assert (await client.get(f"/api/v1/apps/{app['id']}/draft")).json()["has_changes"] is False

    changed = {**config, "system_prompt": "改过的提示词"}
    assert (await put_draft(client, app["id"], 2, changed)).status_code == 200
    assert (await client.get(f"/api/v1/apps/{app['id']}/draft")).json()["has_changes"] is True


async def test_concurrent_save_draft_one_conflict(
    client: AsyncClient, session: AsyncSession, app
) -> None:
    """两个独立会话真并发保存同一 revision：恰好一个成功，另一个 `DRAFT_CONFLICT`。"""
    org = await make_org(session, "并发草稿组织")
    builder = await make_user(session, Role.builder, org, username="concurrent_draft_builder")
    login_as(client, builder)
    created = (await create_app(client)).json()
    config = draft_config()
    cookies = auth_cookies(builder)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async def save_once():
        async with AsyncClient(
            transport=transport, base_url="http://test", cookies=cookies
        ) as concurrent:
            return await concurrent.put(
                f"/api/v1/apps/{created['id']}/draft",
                json={"draft_revision": 1, "config": config},
            )

    first, second = await asyncio.gather(save_once(), save_once())
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    conflict = first if first.status_code == 409 else second
    assert conflict.json()["code"] == "DRAFT_CONFLICT"

    row = await session.get(App, created["id"])
    assert row is not None
    # 只成功保存了一次，revision 只加 1。
    assert row.draft_revision == 2
