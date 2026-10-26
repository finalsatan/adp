"""引用登记与检查测试（P4-04）：知识库缩小可见范围 / 移除协作者前的检查、可添加知识库列表。"""

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.refs import app_can_reference_kb
from app.apps.tables import App
from app.core.schemas import Role
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_embedding_model(session: AsyncSession, owner, *, dim: int = 1024) -> Model:
    model = Model(
        name=f"向量模型-{uuid.uuid4().hex[:8]}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=dim,
        embedding_max_input=8192,
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


async def create_app(client: AsyncClient, *, name: str | None = None, org_id: int | None = None):
    body: dict = {
        "app_type": "kb_qa",
        "name": name or f"应用-{uuid.uuid4().hex[:8]}",
        "visibility": "org",
    }
    if org_id is not None:
        body["org_id"] = org_id
    return await client.post("/api/v1/apps", json=body)


def draft_config(*, kb_ids: tuple[int, ...]) -> dict:
    return {"app_type": "kb_qa", "knowledge": {"kbs": [{"kb_id": kb_id} for kb_id in kb_ids]}}


async def test_app_can_reference_kb_owner_validity(
    client: AsyncClient, session: AsyncSession
) -> None:
    group = await make_org(session, "引用集团")
    org_a = await make_org(session, "引用组织A", parent=group)
    org_b = await make_org(session, "引用组织B", parent=group)
    owner = await make_user(session, Role.builder, org_a, username="ref_owner")
    kb_owner = await make_user(session, Role.builder, org_a, username="ref_kb_owner")
    embedding = await make_embedding_model(session, kb_owner)

    kb_platform = await make_kb(session, org_a, kb_owner, embedding, visibility="platform")
    kb_org = await make_kb(session, org_a, kb_owner, embedding, visibility="org")
    kb_private = await make_kb(session, org_a, owner, embedding, visibility="private")

    login_as(client, owner)
    app_id = (await create_app(client)).json()["id"]
    app = await session.get(App, app_id)
    assert app is not None

    # 创建人有效：按创建人查看条件算。
    assert await app_can_reference_kb(session, app, kb_platform) is True
    assert await app_can_reference_kb(session, app, kb_org) is True
    assert await app_can_reference_kb(session, app, kb_private) is True

    # 创建人调到其他组织：失效，按应用所属组织算；原创建人“仅自己”的不能引用。
    owner.org_id = org_b.id
    await session.commit()
    assert await app_can_reference_kb(session, app, kb_platform) is True
    assert await app_can_reference_kb(session, app, kb_org) is True
    assert await app_can_reference_kb(session, app, kb_private) is False

    # 停用同样算失效。
    owner.status = "disabled"
    await session.commit()
    assert await app_can_reference_kb(session, app, kb_private) is False

    # 调回并重新启用：恢复按创建人判断。
    owner.org_id = org_a.id
    owner.status = "active"
    await session.commit()
    assert await app_can_reference_kb(session, app, kb_private) is True

    # 创建人是启用的系统管理员：按创建人判断（系统管理员能查看所有资源）。
    admin = await make_user(session, Role.sys_admin, username="ref_admin")
    login_as(client, admin)
    admin_app_id = (await create_app(client, org_id=org_a.id)).json()["id"]
    admin_app = await session.get(App, admin_app_id)
    assert admin_app is not None
    assert await app_can_reference_kb(session, admin_app, kb_private) is True


async def test_shrink_visibility_blocked_by_app_ref(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "缩小可见范围组织")
    kb_owner = await make_user(session, Role.builder, org, username="shrink_kb_owner")
    app_owner = await make_user(session, Role.builder, org, username="shrink_app_owner")
    embedding = await make_embedding_model(session, kb_owner)
    kb = await make_kb(session, org, kb_owner, embedding, visibility="org")

    login_as(client, app_owner)
    created = await create_app(client, name="缩小引用应用")
    app_id = created.json()["id"]
    saved = await client.put(
        f"/api/v1/apps/{app_id}/draft",
        json={"draft_revision": 1, "config": draft_config(kb_ids=(kb.id,))},
    )
    assert saved.status_code == 200, saved.text

    login_as(client, kb_owner)
    blocked = await client.patch(f"/api/v1/kbs/{kb.id}/visibility", json={"visibility": "private"})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "RESOURCE_IN_USE"
    assert {"id": app_id, "type": "app", "name": "缩小引用应用"} in blocked.json()["detail"]["refs"]

    # 应用解除引用后可以缩小。
    login_as(client, app_owner)
    removed = await client.put(
        f"/api/v1/apps/{app_id}/draft",
        json={"draft_revision": 2, "config": draft_config(kb_ids=())},
    )
    assert removed.status_code == 200
    login_as(client, kb_owner)
    allowed = await client.patch(f"/api/v1/kbs/{kb.id}/visibility", json={"visibility": "private"})
    assert allowed.status_code == 200


async def test_remove_member_blocked_by_app_ref(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "移除协作者组织")
    kb_owner = await make_user(session, Role.builder, org, username="member_kb_owner")
    app_owner = await make_user(session, Role.builder, org, username="member_app_owner")
    embedding = await make_embedding_model(session, kb_owner)
    kb = await make_kb(session, org, kb_owner, embedding, visibility="private")

    login_as(client, kb_owner)
    added = await client.post(f"/api/v1/kbs/{kb.id}/members", json={"user_id": app_owner.id})
    assert added.status_code == 201

    login_as(client, app_owner)
    app_id = (await create_app(client, name="协作者引用应用")).json()["id"]
    saved = await client.put(
        f"/api/v1/apps/{app_id}/draft",
        json={"draft_revision": 1, "config": draft_config(kb_ids=(kb.id,))},
    )
    assert saved.status_code == 200, saved.text

    login_as(client, kb_owner)
    blocked = await client.delete(f"/api/v1/kbs/{kb.id}/members/{app_owner.id}")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "RESOURCE_IN_USE"
    assert blocked.json()["detail"]["refs"][0]["id"] == app_id


async def test_kb_candidates(client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "候选集团")
    org_a = await make_org(session, "候选组织A", parent=group)
    org_b = await make_org(session, "候选组织B", parent=group)
    app_owner = await make_user(session, Role.builder, org_a, username="cand_app_owner")
    operator = await make_user(session, Role.builder, org_a, username="cand_operator")
    kb_owner = await make_user(session, Role.builder, org_a, username="cand_kb_owner")
    embedding = await make_embedding_model(session, kb_owner)

    kb_platform = await make_kb(
        session, org_a, kb_owner, embedding, visibility="platform", name="候选全平台库"
    )
    kb_private_operator = await make_kb(
        session, org_a, operator, embedding, visibility="private", name="候选私有库"
    )
    kb_org = await make_kb(session, org_a, kb_owner, embedding, visibility="org", name="候选组织库")
    kb_hidden = await make_kb(
        session, org_a, kb_owner, embedding, visibility="private", name="候选隐藏库"
    )

    login_as(client, app_owner)
    app_id = (await create_app(client, name="候选应用")).json()["id"]
    assert (
        await client.post(f"/api/v1/apps/{app_id}/members", json={"user_id": operator.id})
    ).status_code == 201
    assert (
        await client.put(
            f"/api/v1/apps/{app_id}/draft",
            json={"draft_revision": 1, "config": draft_config(kb_ids=(kb_org.id,))},
        )
    ).status_code == 200

    login_as(client, operator)
    listing = await client.get(f"/api/v1/apps/{app_id}/kb-candidates")
    assert listing.status_code == 200, listing.text
    by_id = {item["kb_id"]: item for item in listing.json()}
    # 只列出操作人能查看的：隐藏库不在。
    assert kb_hidden.id not in by_id
    assert set(by_id) == {kb_platform.id, kb_private_operator.id, kb_org.id}

    assert by_id[kb_platform.id]["selectable"] is True
    assert by_id[kb_org.id]["selectable"] is True
    assert by_id[kb_org.id]["added"] is True
    assert by_id[kb_platform.id]["added"] is False
    # 操作人能看、但应用创建人看不到 → 不可选并给出原因。
    assert by_id[kb_private_operator.id]["selectable"] is False
    assert by_id[kb_private_operator.id]["reason"] == "应用创建人 cand_app_owner 不能查看"
    assert by_id[kb_platform.id]["embedding_model"]["id"] == embedding.id

    # 应用创建人失效后按应用所属组织判断：全平台与组织可见可选，原创建人“仅自己”的不可选。
    app_owner.org_id = org_b.id
    await session.commit()
    after = await client.get(f"/api/v1/apps/{app_id}/kb-candidates")
    by_id = {item["kb_id"]: item for item in after.json()}
    assert by_id[kb_platform.id]["selectable"] is True
    assert by_id[kb_org.id]["selectable"] is True
    assert by_id[kb_private_operator.id]["selectable"] is False
    assert by_id[kb_private_operator.id]["reason"] == "应用创建人已失效，应用所属组织不能查看"
