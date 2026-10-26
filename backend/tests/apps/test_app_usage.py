"""使用范围、“我的应用”与终端用户入口测试（P4-05）。

覆盖三档使用范围、系统管理员、停用 / 未发布 / 删除、修改立即生效、`GET /my/apps`、
`GET /chat/apps/{id}` 与使用范围接口的权限和审计。
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps import usage
from app.apps.config import config_hash, default_config
from app.apps.tables import App, AppUsageTarget, AppVersion
from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_user


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


def _config_with_reply(**reply: object) -> dict:
    config = default_config().model_dump(mode="json")
    config["reply"] = {**config["reply"], **reply}
    return config


async def make_app(
    session: AsyncSession,
    org,
    owner,
    *,
    name: str | None = None,
    usage_scope: str = "platform",
    org_targets: tuple[int, ...] = (),
    user_targets: tuple[int, ...] = (),
    published: bool = True,
    enabled: bool = True,
    deleted: bool = False,
    reply: dict | None = None,
) -> tuple[App, Resource]:
    """直接建应用（含发布版本与使用范围名单），供判断函数与接口测试用。"""
    resource = Resource(
        type="app",
        name=name or f"应用-{uuid.uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility="org",
        status="enabled" if enabled else "disabled",
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    session.add(resource)
    await session.flush()
    app = App(
        id=resource.id,
        app_type="kb_qa",
        draft_config=default_config().model_dump(mode="json"),
        draft_revision=1,
        draft_updated_by=owner.id,
        draft_updated_at=datetime.now(UTC),
        usage_scope=usage_scope,
    )
    session.add(app)
    for org_id in org_targets:
        session.add(AppUsageTarget(app_id=resource.id, target_type="org", target_id=org_id))
    for user_id in user_targets:
        session.add(AppUsageTarget(app_id=resource.id, target_type="user", target_id=user_id))
    if published:
        config = _config_with_reply(**reply) if reply else default_config().model_dump(mode="json")
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
    await session.refresh(resource)
    return app, resource


# ---- 判断函数 ----


async def test_usable_scopes(session: AsyncSession, org_tree) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd)
    child = await make_user(session, Role.end_user, org_tree.platform)
    sibling = await make_user(session, Role.end_user, org_tree.product)

    platform_app, _ = await make_app(session, org_tree.rd, owner, usage_scope="platform")
    assert await usage.app_usable_by_user(session, platform_app, owner) is True
    assert await usage.app_usable_by_user(session, platform_app, child) is True
    assert await usage.app_usable_by_user(session, platform_app, sibling) is True

    # orgs 含下级：研发中心的目标，平台组（下级）可用，产品中心（平级）不可用。
    orgs_app, _ = await make_app(
        session, org_tree.rd, owner, usage_scope="orgs", org_targets=(org_tree.rd.id,)
    )
    assert await usage.app_usable_by_user(session, orgs_app, owner) is True
    assert await usage.app_usable_by_user(session, orgs_app, child) is True
    assert await usage.app_usable_by_user(session, orgs_app, sibling) is False

    users_app, _ = await make_app(
        session, org_tree.rd, owner, usage_scope="users", user_targets=(child.id,)
    )
    assert await usage.app_usable_by_user(session, users_app, child) is True
    assert await usage.app_usable_by_user(session, users_app, owner) is False
    assert await usage.app_usable_by_user(session, users_app, sibling) is False


async def test_usable_sys_admin(session: AsyncSession, org_tree) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd)
    admin = await make_user(session, Role.sys_admin)

    platform_app, _ = await make_app(session, org_tree.rd, owner, usage_scope="platform")
    assert await usage.app_usable_by_user(session, platform_app, admin) is True

    orgs_app, _ = await make_app(
        session, org_tree.rd, owner, usage_scope="orgs", org_targets=(org_tree.rd.id,)
    )
    assert await usage.app_usable_by_user(session, orgs_app, admin) is False

    users_app, _ = await make_app(
        session, org_tree.rd, owner, usage_scope="users", user_targets=(admin.id,)
    )
    assert await usage.app_usable_by_user(session, users_app, admin) is True


async def test_usable_requires_active_published_enabled(session: AsyncSession, org_tree) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd)
    disabled_user = await make_user(session, Role.end_user, org_tree.rd, status="disabled")

    app, _ = await make_app(session, org_tree.rd, owner, usage_scope="platform")
    assert await usage.app_usable_by_user(session, app, disabled_user) is False

    unpublished, _ = await make_app(
        session, org_tree.rd, owner, usage_scope="platform", published=False
    )
    assert await usage.app_usable_by_user(session, unpublished, owner) is False

    disabled, _ = await make_app(session, org_tree.rd, owner, usage_scope="platform", enabled=False)
    assert await usage.app_usable_by_user(session, disabled, owner) is False

    deleted, _ = await make_app(session, org_tree.rd, owner, usage_scope="platform", deleted=True)
    assert await usage.app_usable_by_user(session, deleted, owner) is False


# ---- 终端用户入口 ----


async def test_chat_app_errors(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd)
    user = await make_user(session, Role.end_user, org_tree.rd)
    login_as(client, user)

    unpublished, _ = await make_app(session, org_tree.rd, owner, published=False)
    response = await client.get(f"/api/v1/chat/apps/{unpublished.id}")
    assert response.status_code == 403
    assert response.json()["code"] == "APP_NOT_PUBLISHED"

    disabled, _ = await make_app(session, org_tree.rd, owner, enabled=False)
    response = await client.get(f"/api/v1/chat/apps/{disabled.id}")
    assert response.status_code == 403
    assert response.json()["code"] == "APP_DISABLED"

    out_of_scope, _ = await make_app(
        session, org_tree.rd, owner, usage_scope="users", user_targets=(owner.id,)
    )
    response = await client.get(f"/api/v1/chat/apps/{out_of_scope.id}")
    assert response.status_code == 403
    assert response.json()["code"] == "APP_NOT_IN_SCOPE"

    deleted, _ = await make_app(session, org_tree.rd, owner, deleted=True)
    response = await client.get(f"/api/v1/chat/apps/{deleted.id}")
    assert response.status_code == 404

    missing = await client.get("/api/v1/chat/apps/999999")
    assert missing.status_code == 404


async def test_chat_app_info(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd)
    user = await make_user(session, Role.end_user, org_tree.rd)
    login_as(client, user)
    app, _ = await make_app(
        session,
        org_tree.rd,
        owner,
        name="HR 政策问答",
        usage_scope="orgs",
        org_targets=(org_tree.rd.id,),
        reply={
            "opening": "你好，我是 HR 助手。",
            "opening_questions": ["年假有多少天？"],
            "suggestions": True,
            "allow_download": True,
        },
    )
    response = await client.get(f"/api/v1/chat/apps/{app.id}")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": app.id,
        "name": "HR 政策问答",
        "description": None,
        "opening": "你好，我是 HR 助手。",
        "opening_questions": ["年假有多少天？"],
        "suggestions": True,
        "allow_download": True,
    }


async def test_usage_scope_change_immediate(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    """修改使用范围立即生效，不需要重新发布。"""
    owner = await make_user(session, Role.builder, org_tree.rd)
    user = await make_user(session, Role.end_user, org_tree.rd)
    app, _ = await make_app(
        session, org_tree.rd, owner, usage_scope="users", user_targets=(owner.id,)
    )

    login_as(client, user)
    assert (await client.get(f"/api/v1/chat/apps/{app.id}")).status_code == 403

    login_as(client, owner)
    response = await client.put(f"/api/v1/apps/{app.id}/usage-scope", json={"scope": "platform"})
    assert response.status_code == 200, response.text

    login_as(client, user)
    assert (await client.get(f"/api/v1/chat/apps/{app.id}")).status_code == 200


async def test_my_apps_filters_and_sorts(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd)
    user = await make_user(session, Role.end_user, org_tree.rd)
    login_as(client, user)

    await make_app(session, org_tree.rd, owner, name="B 平台应用", usage_scope="platform")
    await make_app(
        session,
        org_tree.rd,
        owner,
        name="A 组织应用",
        usage_scope="orgs",
        org_targets=(org_tree.rd.id,),
    )
    await make_app(
        session, org_tree.rd, owner, name="C 名单应用", usage_scope="users", user_targets=(user.id,)
    )
    # 不可用：未发布、已停用、平级组织、不在名单。
    await make_app(
        session, org_tree.rd, owner, name="Z 未发布", usage_scope="platform", published=False
    )
    await make_app(
        session, org_tree.rd, owner, name="Y 已停用", usage_scope="platform", enabled=False
    )
    await make_app(
        session,
        org_tree.rd,
        owner,
        name="X 平级组织",
        usage_scope="orgs",
        org_targets=(org_tree.product.id,),
    )
    await make_app(
        session,
        org_tree.rd,
        owner,
        name="W 别人名单",
        usage_scope="users",
        user_targets=(owner.id,),
    )

    response = await client.get("/api/v1/my/apps")
    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["name"] for item in items] == ["A 组织应用", "B 平台应用", "C 名单应用"]
    first = items[0]
    assert first["app_type"] == "kb_qa"
    assert first["description"] is None
    assert first["org"] == {"id": org_tree.rd.id, "name": org_tree.rd.name}
    assert first["chat_path"] == f"/chat/{first['id']}"


# ---- 使用范围接口 ----


async def test_usage_scope_api_and_audit(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    builder = await make_user(session, Role.builder, org_tree.rd)
    target_user = await make_user(session, Role.end_user, org_tree.product)
    login_as(client, builder)
    created = await client.post(
        "/api/v1/apps",
        json={"app_type": "kb_qa", "name": f"应用-{uuid.uuid4().hex[:6]}", "visibility": "org"},
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]

    # 默认：本组织。
    current = await client.get(f"/api/v1/apps/{app_id}/usage-scope")
    assert current.status_code == 200
    assert current.json() == {
        "scope": "orgs",
        "orgs": [{"id": org_tree.rd.id, "name": org_tree.rd.name}],
        "users": [],
    }

    # 改成指定用户。
    response = await client.put(
        f"/api/v1/apps/{app_id}/usage-scope",
        json={"scope": "users", "user_ids": [target_user.id]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "scope": "users",
        "orgs": [],
        "users": [
            {"id": target_user.id, "username": target_user.username, "name": target_user.name}
        ],
    }
    stored = await session.get(App, app_id)
    assert stored is not None and stored.usage_scope == "users"
    targets = (
        (await session.execute(sa.select(AppUsageTarget).where(AppUsageTarget.app_id == app_id)))
        .scalars()
        .all()
    )
    assert [(t.target_type, t.target_id) for t in targets] == [("user", target_user.id)]

    # 审计：app.change_usage_scope，detail 含 from、to。
    log = (
        await session.execute(
            sa.select(AuditLog).where(
                AuditLog.action == "app.change_usage_scope",
                AuditLog.target_id == app_id,
            )
        )
    ).scalar_one()
    assert log.detail["from"]["scope"] == "orgs"
    assert log.detail["to"]["scope"] == "users"

    # 校验：全平台带列表、指定组织空列表、指定用户空列表都 422。
    for body in (
        {"scope": "platform", "org_ids": [org_tree.rd.id]},
        {"scope": "orgs", "org_ids": []},
        {"scope": "users", "user_ids": []},
    ):
        invalid = await client.put(f"/api/v1/apps/{app_id}/usage-scope", json=body)
        assert invalid.status_code == 422, invalid.text

    # 组织 / 用户不存在 → 404。
    missing_user = await client.put(
        f"/api/v1/apps/{app_id}/usage-scope", json={"scope": "users", "user_ids": [999999]}
    )
    assert missing_user.status_code == 404
    missing_org = await client.put(
        f"/api/v1/apps/{app_id}/usage-scope", json={"scope": "orgs", "org_ids": [999999]}
    )
    assert missing_org.status_code == 404


async def test_usage_scope_permissions(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd)
    collaborator = await make_user(session, Role.builder, org_tree.rd)
    outsider = await make_user(session, Role.builder, org_tree.product)
    end_user = await make_user(session, Role.end_user, org_tree.rd)

    login_as(client, owner)
    created = await client.post(
        "/api/v1/apps",
        json={
            "app_type": "kb_qa",
            "name": f"私有应用-{uuid.uuid4().hex[:6]}",
            "visibility": "private",
        },
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]
    added = await client.post(f"/api/v1/apps/{app_id}/members", json={"user_id": collaborator.id})
    assert added.status_code == 201, added.text

    # 协作者能看不能管。
    login_as(client, collaborator)
    assert (await client.get(f"/api/v1/apps/{app_id}/usage-scope")).status_code == 200
    denied = await client.put(f"/api/v1/apps/{app_id}/usage-scope", json={"scope": "platform"})
    assert denied.status_code == 403

    # 看不到的人 404。
    login_as(client, outsider)
    assert (await client.get(f"/api/v1/apps/{app_id}/usage-scope")).status_code == 404
    assert (
        await client.put(f"/api/v1/apps/{app_id}/usage-scope", json={"scope": "platform"})
    ).status_code == 404

    # 终端用户调控制台接口 403（没有 console.use）；PUT 走资源“管理”判断，
    # 看不到这个私有应用，按 404 处理（与 P4-03 的编辑接口一致）。
    login_as(client, end_user)
    assert (await client.get(f"/api/v1/apps/{app_id}/usage-scope")).status_code == 403
    assert (
        await client.put(f"/api/v1/apps/{app_id}/usage-scope", json={"scope": "platform"})
    ).status_code == 404
    # 但能用“我的应用”和终端用户接口（需要已发布，这里只看权限没被拦）。
    assert (await client.get("/api/v1/my/apps")).status_code == 200
