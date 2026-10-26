"""应用管理接口测试（P4-03）：创建、列表、详情、编辑、停用启用、可见范围、协作者与权限。"""

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import config_hash, default_config
from app.apps.tables import App, AppUsageTarget, AppVersion
from app.audit.tables import AuditLog
from app.core.schemas import Role
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user) -> None:
    """在 client 实例上设 Cookie，供后续请求使用。"""
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def create_app(
    client: AsyncClient,
    *,
    name: str,
    visibility: str = "org",
    app_type: str = "kb_qa",
    org_id: int | None = None,
    description: str | None = None,
):
    body: dict = {"app_type": app_type, "name": name, "visibility": visibility}
    if org_id is not None:
        body["org_id"] = org_id
    if description is not None:
        body["description"] = description
    return await client.post("/api/v1/apps", json=body)


async def publish_app(
    session: AsyncSession, app_id: int, config_hash_value: str, publisher_id: int = 1
) -> AppVersion:
    """直接插一个发布版本并指回应用，供状态推导测试用（发布接口在 P4-04）。"""
    version = AppVersion(
        app_id=app_id,
        version_no=1,
        config=default_config().model_dump(mode="json"),
        config_hash=config_hash_value,
        note="初版",
        published_by=publisher_id,
    )
    session.add(version)
    await session.flush()
    app = await session.get(App, app_id)
    assert app is not None
    app.published_version_id = version.id
    await session.commit()
    return version


async def audit_actions(session: AsyncSession, target_id: int) -> list[str]:
    rows = (
        (
            await session.execute(
                sa.select(AuditLog.action)
                .where(AuditLog.target_type == "app", AuditLog.target_id == target_id)
                .order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def test_create_list_detail(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "应用组织")
    builder = await make_user(session, Role.builder, org, username="app_builder")
    login_as(client, builder)

    created = await create_app(client, name="客服助手", description="描述")
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["app_type"] == "kb_qa"
    assert data["org"] == {"id": org.id, "name": org.name}
    assert data["owner"]["id"] == builder.id
    assert data["visibility"] == "org"
    assert data["status"] == "unpublished"
    assert data["published_version"] is None
    assert data["draft_has_changes"] is True
    assert data["draft_updated_at"] is not None
    assert data["usage_scope"] == {
        "scope": "orgs",
        "orgs": [{"id": org.id, "name": org.name}],
        "users": [],
    }
    assert data["chat_path"] == f"/chat/{data['id']}"
    assert data["editable"] is True and data["manageable"] is True

    app_row = await session.get(App, data["id"])
    assert app_row is not None
    assert app_row.draft_config == default_config().model_dump(mode="json")
    assert app_row.draft_revision == 1
    assert app_row.draft_updated_by == builder.id
    assert app_row.usage_scope == "orgs"
    assert app_row.published_version_id is None

    # 默认使用范围：应用所属组织。
    targets = (
        (
            await session.execute(
                sa.select(AppUsageTarget).where(AppUsageTarget.app_id == data["id"])
            )
        )
        .scalars()
        .all()
    )
    assert len(targets) == 1
    assert targets[0].target_type == "org"
    assert targets[0].target_id == org.id

    listing = await client.get("/api/v1/apps")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == data["id"]

    detail = await client.get(f"/api/v1/apps/{data['id']}")
    assert detail.status_code == 200
    assert detail.json()["name"] == "客服助手"
    assert detail.json()["description"] == "描述"

    assert "app.create" in await audit_actions(session, data["id"])


async def test_status_and_draft_has_changes(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "状态组织")
    builder = await make_user(session, Role.builder, org, username="status_app_builder")
    login_as(client, builder)
    app = (await create_app(client, name="状态应用")).json()

    version = await publish_app(session, app["id"], config_hash(default_config()))

    detail = await client.get(f"/api/v1/apps/{app['id']}")
    body = detail.json()
    assert body["status"] == "published"
    assert body["published_version"]["id"] == version.id
    assert body["published_version"]["version_no"] == 1
    assert body["published_version"]["published_at"] is not None
    # 草稿哈希与发布版本一致 → 无未发布修改。
    assert body["draft_has_changes"] is False

    app_row = await session.get(App, app["id"])
    assert app_row is not None
    app_row.draft_config = {
        **default_config().model_dump(mode="json"),
        "system_prompt": "改过的提示词",
    }
    await session.commit()

    changed = await client.get(f"/api/v1/apps/{app['id']}")
    assert changed.json()["draft_has_changes"] is True

    disabled = await client.post(f"/api/v1/apps/{app['id']}:disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["published_version"]["id"] == version.id


async def test_duplicate_name_and_org_required(client: AsyncClient, session: AsyncSession) -> None:
    org_a = await make_org(session, "重名组织A")
    org_b = await make_org(session, "重名组织B")
    admin = await make_user(session, Role.sys_admin, username="app_dup_admin")
    login_as(client, admin)

    # 系统管理员必须给 org_id。
    missing_org = await create_app(client, name="没有组织")
    assert missing_org.status_code == 422
    assert missing_org.json()["code"] == "ORG_REQUIRED"

    first = await create_app(client, name="同名应用", org_id=org_a.id)
    assert first.status_code == 201, first.text
    duplicate = await create_app(client, name="同名应用", org_id=org_a.id)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "NAME_CONFLICT"
    other_org = await create_app(client, name="同名应用", org_id=org_b.id)
    assert other_org.status_code == 201


async def test_update_immutable_and_audit(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "编辑组织")
    builder = await make_user(session, Role.builder, org, username="edit_app_builder")
    login_as(client, builder)
    app = (await create_app(client, name="旧名称")).json()

    type_change = await client.patch(f"/api/v1/apps/{app['id']}", json={"app_type": "kb_qa"})
    assert type_change.status_code == 422
    assert type_change.json()["code"] == "APP_TYPE_IMMUTABLE"

    renamed = await client.patch(
        f"/api/v1/apps/{app['id']}", json={"name": "新名称", "description": "新描述"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "新名称"
    assert renamed.json()["description"] == "新描述"

    logs = (
        (
            await session.execute(
                sa.select(AuditLog).where(
                    AuditLog.target_type == "app", AuditLog.target_id == app["id"]
                )
            )
        )
        .scalars()
        .all()
    )
    update_log = next(log for log in logs if log.action == "app.update")
    assert update_log.target_desc == "新名称"
    assert update_log.target_org_id == org.id

    # 同组织内改名冲突。
    await create_app(client, name="占用的名字")
    conflict = await client.patch(f"/api/v1/apps/{app['id']}", json={"name": "占用的名字"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "NAME_CONFLICT"


async def test_disable_enable_visibility_members_audit(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "协作者组织")
    owner = await make_user(session, Role.builder, org, username="app_collab_owner")
    member = await make_user(session, Role.builder, org, username="app_collab_member")
    login_as(client, owner)
    app = (await create_app(client, name="协作者应用", visibility="private")).json()

    disabled = await client.post(f"/api/v1/apps/{app['id']}:disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    enabled = await client.post(f"/api/v1/apps/{app['id']}:enable")
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "unpublished"

    added = await client.post(f"/api/v1/apps/{app['id']}/members", json={"user_id": member.id})
    assert added.status_code == 201, added.text
    assert added.json()["user"]["id"] == member.id
    listing = await client.get(f"/api/v1/apps/{app['id']}/members")
    assert listing.status_code == 200
    assert [row["user"]["id"] for row in listing.json()] == [member.id]

    changed = await client.patch(f"/api/v1/apps/{app['id']}/visibility", json={"visibility": "org"})
    assert changed.status_code == 200
    assert changed.json()["visibility"] == "org"

    removed = await client.delete(f"/api/v1/apps/{app['id']}/members/{member.id}")
    assert removed.status_code == 204

    actions = await audit_actions(session, app["id"])
    assert {
        "app.disable",
        "app.enable",
        "app.add_member",
        "app.change_visibility",
        "app.remove_member",
    } <= set(actions)


async def test_view_edit_manage_permissions(client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "应用权限集团")
    rd = await make_org(session, "应用权限研发", parent=group)
    product = await make_org(session, "应用权限产品", parent=group)
    owner = await make_user(session, Role.builder, rd, username="app_perm_owner")
    viewer = await make_user(session, Role.builder, rd, username="app_perm_viewer")
    outsider = await make_user(session, Role.builder, product, username="app_perm_outsider")

    login_as(client, owner)
    app = (await create_app(client, name="权限应用", visibility="org")).json()

    # 能看不能管：查看 200，编辑 / 管理类操作 403。
    login_as(client, viewer)
    assert (await client.get("/api/v1/apps")).json()["total"] == 1
    assert (await client.get(f"/api/v1/apps/{app['id']}")).status_code == 200
    assert (await client.get(f"/api/v1/apps/{app['id']}/members")).status_code == 200
    for method, path, kwargs in (
        ("patch", f"/api/v1/apps/{app['id']}", {"json": {"name": "改名"}}),
        ("post", f"/api/v1/apps/{app['id']}:disable", {}),
        ("post", f"/api/v1/apps/{app['id']}:enable", {}),
        ("patch", f"/api/v1/apps/{app['id']}/visibility", {"json": {"visibility": "platform"}}),
        ("post", f"/api/v1/apps/{app['id']}/members", {"json": {"user_id": owner.id}}),
        ("delete", f"/api/v1/apps/{app['id']}/members/{owner.id}", {}),
        ("delete", f"/api/v1/apps/{app['id']}", {}),
    ):
        response = await getattr(client, method)(path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)

    # 看不到：详情与操作都 404（不暴露是否存在）。
    login_as(client, outsider)
    assert (await client.get(f"/api/v1/apps/{app['id']}")).status_code == 404
    assert (await client.post(f"/api/v1/apps/{app['id']}:disable")).status_code == 404
    assert (await client.get(f"/api/v1/apps/{app['id']}/members")).status_code == 404


async def test_end_user_forbidden(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "应用终端用户组织")
    end_user = await make_user(session, Role.end_user, org, username="app_end_user")
    owner = await make_user(session, Role.builder, org, username="app_end_owner")
    login_as(client, owner)
    app = (await create_app(client, name="终端应用")).json()

    login_as(client, end_user)
    assert (await client.get("/api/v1/apps")).status_code == 403
    assert (await client.get(f"/api/v1/apps/{app['id']}")).status_code == 403
    create = await create_app(client, name="终端建应用")
    assert create.status_code == 403
    assert create.json()["code"] == "PERMISSION_DENIED"


async def test_list_filters_and_keyword_escape(client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "应用筛选集团")
    rd = await make_org(session, "应用筛选研发", parent=group)
    product = await make_org(session, "应用筛选产品", parent=group)
    admin = await make_user(session, Role.sys_admin, username="app_filter_admin")
    login_as(client, admin)

    await create_app(client, name="员工制度应用", org_id=rd.id, visibility="org")
    disabled = await create_app(
        client, name="常见问题应用", org_id=product.id, visibility="platform"
    )
    assert disabled.status_code == 201
    await client.post(f"/api/v1/apps/{disabled.json()['id']}:disable")
    published = await create_app(client, name="发布应用", org_id=rd.id, visibility="org")
    assert published.status_code == 201
    await publish_app(session, published.json()["id"], config_hash(default_config()))

    by_type = await client.get("/api/v1/apps", params={"app_type": "kb_qa"})
    assert by_type.json()["total"] == 3

    unpublished = await client.get("/api/v1/apps", params={"status": "unpublished"})
    assert unpublished.json()["total"] == 1
    assert unpublished.json()["items"][0]["name"] == "员工制度应用"

    by_published = await client.get("/api/v1/apps", params={"status": "published"})
    assert by_published.json()["total"] == 1
    assert by_published.json()["items"][0]["name"] == "发布应用"

    by_disabled = await client.get("/api/v1/apps", params={"status": "disabled"})
    assert by_disabled.json()["total"] == 1
    assert by_disabled.json()["items"][0]["name"] == "常见问题应用"

    by_visibility = await client.get("/api/v1/apps", params={"visibility": "org"})
    assert by_visibility.json()["total"] == 2

    # 含下级：按集团筛选能查到子组织；不含下级查不到。
    with_sub = await client.get("/api/v1/apps", params={"org_id": group.id, "include_sub": True})
    assert with_sub.json()["total"] == 3
    without_sub = await client.get(
        "/api/v1/apps", params={"org_id": group.id, "include_sub": False}
    )
    assert without_sub.json()["total"] == 0

    # LIKE 通配符要转义：输入 % 不应匹配全部。
    wildcard = await client.get("/api/v1/apps", params={"keyword": "%"})
    assert wildcard.json()["total"] == 0
    matched = await client.get("/api/v1/apps", params={"keyword": "员工"})
    assert matched.json()["total"] == 1
    assert matched.json()["items"][0]["name"] == "员工制度应用"
