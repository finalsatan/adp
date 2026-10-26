"""组织管理接口测试。"""

from datetime import UTC, datetime
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.iam.tables import Org, User
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user: User) -> None:
    """在 client 实例上设 Cookie，供后续请求使用。"""
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_resource(session: AsyncSession, org: Org, owner: User) -> Resource:
    resource = Resource(
        type="kb",
        name=f"r{org.id}",
        org_id=org.id,
        owner_id=owner.id,
        visibility="org",
        status="enabled",
    )
    session.add(resource)
    await session.commit()
    return resource


@pytest_asyncio.fixture
async def sys_admin(session: AsyncSession) -> User:
    return await make_user(session, Role.sys_admin, username="sysadmin")


@pytest_asyncio.fixture
async def rd_admin(session: AsyncSession, org_tree) -> User:
    return await make_user(session, Role.org_admin, org_tree.rd, username="rdadmin")


async def test_sys_admin_creates_top_level(
    client: AsyncClient, session: AsyncSession, sys_admin: User, rd_admin: User
) -> None:
    login_as(client, sys_admin)
    response = await client.post("/api/v1/orgs", json={"name": " 华东子公司 "})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "华东子公司"
    assert body["parent_id"] is None
    assert body["path"] == f"/{body['id']}/"
    assert body["editable"] is True
    assert body["admins"] == []
    assert (body["user_count"], body["resource_count"], body["child_count"]) == (0, 0, 0)

    login_as(client, rd_admin)
    denied = await client.post("/api/v1/orgs", json={"name": "另一个顶级"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_DENIED"


async def test_org_admin_create_scope(client: AsyncClient, org_tree, rd_admin: User) -> None:
    login_as(client, rd_admin)

    own = await client.post("/api/v1/orgs", json={"parent_id": org_tree.rd.id, "name": "测试组"})
    assert own.status_code == 201
    assert own.json()["path"].startswith(org_tree.rd.path)

    below = await client.post(
        "/api/v1/orgs",
        json={"parent_id": org_tree.platform.id, "name": "平台子组"},
    )
    assert below.status_code == 201

    sibling = await client.post(
        "/api/v1/orgs", json={"parent_id": org_tree.product.id, "name": "x"}
    )
    assert sibling.status_code == 403

    parent = await client.post("/api/v1/orgs", json={"parent_id": org_tree.group.id, "name": "y"})
    assert parent.status_code == 403


async def test_org_admin_edit_delete_scope(
    client: AsyncClient, session: AsyncSession, org_tree, rd_admin: User
) -> None:
    login_as(client, rd_admin)

    own_patch = await client.patch(f"/api/v1/orgs/{org_tree.rd.id}", json={"name": "研发中心X"})
    assert own_patch.status_code == 403
    own_delete = await client.delete(f"/api/v1/orgs/{org_tree.rd.id}")
    assert own_delete.status_code == 403

    child = await client.patch(
        f"/api/v1/orgs/{org_tree.platform.id}",
        json={"name": "平台组X", "remark": "备注"},
    )
    assert child.status_code == 200
    assert child.json()["name"] == "平台组X"
    assert child.json()["editable"] is True

    # 上级不可修改：请求里带 parent_id 被校验错误拒绝
    with_parent = await client.patch(
        f"/api/v1/orgs/{org_tree.platform.id}",
        json={"parent_id": org_tree.rd.id, "name": "平台组Y"},
    )
    assert with_parent.status_code == 422

    empty = await make_org(session, "空组", parent=org_tree.rd)
    deleted = await client.delete(f"/api/v1/orgs/{empty.id}")
    assert deleted.status_code == 204


async def test_list_scope_and_editable(
    client: AsyncClient, org_tree, rd_admin: User, sys_admin: User
) -> None:
    login_as(client, rd_admin)
    response = await client.get("/api/v1/orgs")
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}
    assert set(rows) == {org_tree.rd.id, org_tree.platform.id, org_tree.algo.id}
    assert rows[org_tree.rd.id]["editable"] is False
    assert rows[org_tree.platform.id]["editable"] is True
    # 直属的组织管理员
    assert [a["username"] for a in rows[org_tree.rd.id]["admins"]] == ["rdadmin"]

    login_as(client, sys_admin)
    all_rows = await client.get("/api/v1/orgs")
    assert {row["id"] for row in all_rows.json()} == {
        org_tree.group.id,
        org_tree.rd.id,
        org_tree.platform.id,
        org_tree.algo.id,
        org_tree.product.id,
    }


async def test_name_conflicts(client: AsyncClient, org_tree, sys_admin: User) -> None:
    login_as(client, sys_admin)

    first = await client.post("/api/v1/orgs", json={"name": "同名"})
    assert first.status_code == 201
    duplicate = await client.post("/api/v1/orgs", json={"name": "同名"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "NAME_CONFLICT"

    same_parent = await client.post(
        "/api/v1/orgs", json={"parent_id": org_tree.group.id, "name": "研发中心"}
    )
    assert same_parent.status_code == 409

    other_parent = await client.post(
        "/api/v1/orgs", json={"parent_id": org_tree.product.id, "name": "研发中心"}
    )
    assert other_parent.status_code == 201

    removed = await client.delete(f"/api/v1/orgs/{first.json()['id']}")
    assert removed.status_code == 204
    again = await client.post("/api/v1/orgs", json={"name": "同名"})
    assert again.status_code == 201


async def test_delete_org_not_empty(
    client: AsyncClient, session: AsyncSession, org_tree, sys_admin: User
) -> None:
    login_as(client, sys_admin)

    # 有下级组织
    response = await client.delete(f"/api/v1/orgs/{org_tree.rd.id}")
    assert response.status_code == 409
    assert response.json()["code"] == "ORG_NOT_EMPTY"
    assert response.json()["detail"] == {"children": 2, "users": 0, "resources": 0}

    # 有用户（含停用用户）
    await make_user(session, Role.builder, org_tree.rd, status="disabled")
    response = await client.delete(f"/api/v1/orgs/{org_tree.rd.id}")
    assert response.json()["detail"]["users"] == 1

    # 有资源
    owner = await make_user(session, Role.builder, org_tree.platform)
    await make_resource(session, org_tree.platform, owner)
    response = await client.delete(f"/api/v1/orgs/{org_tree.platform.id}")
    assert response.json()["detail"]["resources"] == 1

    # 都没有时删除成功，列表不再出现
    empty = await make_org(session, "空组", parent=org_tree.rd)
    deleted = await client.delete(f"/api/v1/orgs/{empty.id}")
    assert deleted.status_code == 204
    listed = await client.get("/api/v1/orgs")
    assert empty.id not in {row["id"] for row in listed.json()}


async def test_members_direct_only(
    client: AsyncClient, session: AsyncSession, org_tree, rd_admin: User
) -> None:
    await make_user(session, Role.builder, org_tree.rd, username="rd_builder")
    await make_user(session, Role.end_user, org_tree.rd, username="rd_disabled", status="disabled")
    await make_user(session, Role.builder, org_tree.platform, username="plat_builder")

    login_as(client, rd_admin)
    response = await client.get(f"/api/v1/orgs/{org_tree.rd.id}/members")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    by_name = {row["username"]: row for row in body["items"]}
    assert set(by_name) == {"rdadmin", "rd_builder", "rd_disabled"}
    assert by_name["rdadmin"]["manageable"] is False
    assert by_name["rd_builder"]["manageable"] is True
    assert by_name["rd_disabled"]["status"] == "disabled"

    denied = await client.get(f"/api/v1/orgs/{org_tree.product.id}/members")
    assert denied.status_code == 403


async def test_builder_forbidden(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    builder = await make_user(session, Role.builder, org_tree.rd)
    login_as(client, builder)
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", "/api/v1/orgs", None),
        ("POST", "/api/v1/orgs", {"name": "x"}),
        ("PATCH", f"/api/v1/orgs/{org_tree.platform.id}", {"name": "x"}),
        ("DELETE", f"/api/v1/orgs/{org_tree.platform.id}", None),
        ("GET", f"/api/v1/orgs/{org_tree.platform.id}/members", None),
    ]
    for method, path, payload in calls:
        response = await client.request(method, path, json=payload)
        assert response.status_code == 403, (method, path)
        assert response.json()["code"] == "PERMISSION_DENIED"


async def test_audits(
    client: AsyncClient,
    session: AsyncSession,
    org_tree,
    sys_admin: User,
    rd_admin: User,
) -> None:
    login_as(client, sys_admin)
    created = await client.post(
        "/api/v1/orgs",
        json={"parent_id": org_tree.group.id, "name": "审计组"},
    )
    org_id = created.json()["id"]
    updated = await client.patch(
        f"/api/v1/orgs/{org_id}",
        json={"name": "审计组2", "remark": "r"},
    )
    assert updated.status_code == 200
    deleted = await client.delete(f"/api/v1/orgs/{org_id}")
    assert deleted.status_code == 204

    logs = (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    by_action = {log.action: log for log in logs}
    assert by_action["org.create"].result == "success"
    assert by_action["org.create"].target_id == org_id
    assert by_action["org.create"].target_desc == "审计组"
    assert by_action["org.update"].detail == {
        "name": ["审计组", "审计组2"],
        "remark": [None, "r"],
    }
    assert by_action["org.delete"].result == "success"

    # 被拒绝的操作记 failure
    login_as(client, rd_admin)
    await client.post("/api/v1/orgs", json={"name": "拒绝"})
    await client.patch(f"/api/v1/orgs/{org_tree.rd.id}", json={"name": "x"})
    await client.delete(f"/api/v1/orgs/{org_tree.rd.id}")

    failures = (
        (await session.execute(select(AuditLog).where(AuditLog.result == "failure")))
        .scalars()
        .all()
    )
    seen = {(log.action, (log.detail or {}).get("code")) for log in failures}
    assert ("org.create", "PERMISSION_DENIED") in seen
    assert ("org.update", "PERMISSION_DENIED") in seen
    assert ("org.delete", "PERMISSION_DENIED") in seen


async def test_org_tree_returns_structure_for_builder(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    """搭建者（只有 console.use）能调，只拿到本人组织子树的结构字段。"""
    builder = await make_user(session, Role.builder, org=org_tree.rd, username="treebuilder")
    removed = await make_org(session, "已删子组", parent=org_tree.rd)
    removed.deleted_at = datetime.now(UTC)
    await session.commit()

    login_as(client, builder)
    response = await client.get("/api/v1/orgs:tree")
    assert response.status_code == 200
    rows = response.json()

    # 本人组织子树，不含平级/上级组织，也不含已删除组织。
    assert {row["id"] for row in rows} == {
        org_tree.rd.id,
        org_tree.platform.id,
        org_tree.algo.id,
    }
    # 只有结构信息，没有成员、管理员等。
    assert all(set(row) == {"id", "parent_id", "name"} for row in rows)
    by_id = {row["id"]: row for row in rows}
    assert by_id[org_tree.rd.id] == {
        "id": org_tree.rd.id,
        "parent_id": org_tree.group.id,
        "name": "研发中心",
    }
    assert by_id[org_tree.platform.id]["parent_id"] == org_tree.rd.id


async def test_org_tree_requires_login(client: AsyncClient) -> None:
    response = await client.get("/api/v1/orgs:tree")
    assert response.status_code == 401


async def test_org_tree_denied_for_end_user(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    end_user = await make_user(session, Role.end_user, org=org_tree.rd, username="treeuser")
    login_as(client, end_user)
    response = await client.get("/api/v1/orgs:tree")
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
