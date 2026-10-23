"""用户管理接口测试：权限范围、校验、启停与令牌、角色调整、审计、搜索。"""

import json

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.iam.tables import User
from tests.factories import DEFAULT_PASSWORD, auth_cookies, make_user


def login_as(client: AsyncClient, user: User) -> None:
    """在 client 实例上设 Cookie，供后续请求使用。"""
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def _rd_admin(session: AsyncSession, org_tree) -> User:
    return await make_user(session, Role.org_admin, org_tree.rd, username="rdadmin")


async def test_org_admin_manage_scope(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    await make_user(session, Role.builder, org_tree.rd, username="rd_builder")
    await make_user(session, Role.end_user, org_tree.rd, username="rd_end")
    plat_admin = await make_user(session, Role.org_admin, org_tree.platform, username="plat_admin")
    await make_user(session, Role.builder, org_tree.platform, username="plat_builder")
    await make_user(session, Role.end_user, org_tree.algo, username="algo_end")

    login_as(client, rd_admin)
    listing = await client.get("/api/v1/users")
    assert listing.status_code == 200
    assert {row["username"] for row in listing.json()["items"]} == {
        "rdadmin",
        "rd_builder",
        "rd_end",
        "plat_admin",
        "plat_builder",
        "algo_end",
    }

    # 下级组织的组织管理员可以管理
    detail = await client.get(f"/api/v1/users/{plat_admin.id}")
    assert detail.status_code == 200
    assert detail.json()["manageable"] is True
    edited = await client.patch(f"/api/v1/users/{plat_admin.id}", json={"name": "平台管理员"})
    assert edited.status_code == 200
    reset = await client.post(
        f"/api/v1/users/{plat_admin.id}:reset-password", json={"new_password": "NewPass@123"}
    )
    assert reset.status_code == 204
    unlock = await client.post(f"/api/v1/users/{plat_admin.id}:unlock")
    assert unlock.status_code == 204
    disabled = await client.post(f"/api/v1/users/{plat_admin.id}:disable")
    assert disabled.status_code == 200


async def test_org_admin_cannot_manage_peers_superiors_sysadmin(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    peer = await make_user(session, Role.org_admin, org_tree.rd, username="peer_admin")
    superior = await make_user(session, Role.org_admin, org_tree.group, username="group_admin")
    sys_admin = await make_user(session, Role.sys_admin, username="sysadmin")
    outsider = await make_user(session, Role.builder, org_tree.product, username="product_builder")

    login_as(client, rd_admin)
    # 同级组织管理员可以看详情，但不能操作
    assert (await client.get(f"/api/v1/users/{peer.id}")).status_code == 200
    for target in (peer, superior, sys_admin, outsider):
        for method, path, body in [
            ("PATCH", f"/api/v1/users/{target.id}", {"name": "x"}),
            ("POST", f"/api/v1/users/{target.id}:disable", None),
            ("POST", f"/api/v1/users/{target.id}:reset-password", {"new_password": "NewPass@123"}),
            ("POST", f"/api/v1/users/{target.id}:unlock", None),
        ]:
            response = await client.request(method, path, json=body)
            assert response.status_code == 403, (target.username, method)
            assert response.json()["code"] == "PERMISSION_DENIED"

    # 详情：范围外或系统管理员按 404 处理
    for target in (superior, sys_admin, outsider):
        assert (await client.get(f"/api/v1/users/{target.id}")).status_code == 404


async def test_org_admin_only_assigns_subordinate_org_admin(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    login_as(client, rd_admin)

    denied_create = await client.post(
        "/api/v1/users",
        json={
            "username": "newadmin",
            "password": "Secret@123",
            "name": "新管理员",
            "org_id": org_tree.rd.id,
            "role": "org_admin",
        },
    )
    assert denied_create.status_code == 403

    ok_create = await client.post(
        "/api/v1/users",
        json={
            "username": "subadmin",
            "password": "Secret@123",
            "name": "下级管理员",
            "org_id": org_tree.platform.id,
            "role": "org_admin",
        },
    )
    assert ok_create.status_code == 201
    assert ok_create.json()["role"] == "org_admin"

    rd_builder = await make_user(session, Role.builder, org_tree.rd, username="rd_builder")
    denied_patch = await client.patch(f"/api/v1/users/{rd_builder.id}", json={"role": "org_admin"})
    assert denied_patch.status_code == 403

    sub_builder = await make_user(session, Role.builder, org_tree.platform, username="plat_builder")
    ok_patch = await client.patch(f"/api/v1/users/{sub_builder.id}", json={"role": "org_admin"})
    assert ok_patch.status_code == 200
    assert ok_patch.json()["role"] == "org_admin"
    assert ok_patch.json()["manageable"] is True


async def test_change_org_out_of_scope(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    target = await make_user(session, Role.builder, org_tree.platform, username="mover")

    login_as(client, rd_admin)
    response = await client.patch(
        f"/api/v1/users/{target.id}", json={"org_id": org_tree.product.id}
    )
    assert response.status_code == 403


async def test_username_case_and_weak_password(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    login_as(client, rd_admin)

    created = await client.post(
        "/api/v1/users",
        json={
            "username": "Alice",
            "password": "Secret@123",
            "name": "爱丽丝",
            "org_id": org_tree.platform.id,
            "role": "builder",
        },
    )
    assert created.status_code == 201
    assert created.json()["username"] == "alice"

    duplicate = await client.post(
        "/api/v1/users",
        json={
            "username": "aLiCe",
            "password": "Secret@123",
            "name": "另一个",
            "org_id": org_tree.platform.id,
            "role": "builder",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "USERNAME_EXISTS"

    weak = await client.post(
        "/api/v1/users",
        json={
            "username": "weakuser",
            "password": "short",
            "name": "弱密码",
            "org_id": org_tree.platform.id,
            "role": "builder",
        },
    )
    assert weak.status_code == 422
    assert weak.json()["code"] == "WEAK_PASSWORD"
    assert "TOO_SHORT" in weak.json()["detail"]["reasons"]

    same_as_username = await client.post(
        "/api/v1/users",
        json={
            "username": "sameuser",
            "password": "sameuser",
            "name": "同名密码",
            "org_id": org_tree.platform.id,
            "role": "builder",
        },
    )
    assert same_as_username.status_code == 422
    assert "SAME_AS_USERNAME" in same_as_username.json()["detail"]["reasons"]


async def test_disable_invalidates_tokens(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    target = await make_user(session, Role.builder, org_tree.platform, username="tokenuser")

    login = await client.post(
        "/api/v1/auth/login", json={"username": "tokenuser", "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200
    access = client.cookies.get("adp_at")
    refresh = client.cookies.get("adp_rt")
    assert access and refresh

    login_as(client, rd_admin)
    disabled = await client.post(f"/api/v1/users/{target.id}:disable")
    assert disabled.status_code == 200

    # 原访问令牌立即 401（上下文缓存已随 perm_version 失效）
    client.cookies.clear()
    client.cookies.set("adp_at", access, path="/api")
    me = await client.get("/api/v1/me")
    assert me.status_code == 401
    assert me.json()["code"] == "AUTH_REQUIRED"

    # 刷新令牌也不可用
    client.cookies.clear()
    client.cookies.set("adp_rt", refresh, path="/api/v1/auth/refresh")
    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 401
    assert refreshed.json()["code"] == "REFRESH_INVALID"


async def test_role_change_without_relogin(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    target = await make_user(session, Role.builder, org_tree.platform, username="promote")

    login = await client.post(
        "/api/v1/auth/login", json={"username": "promote", "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200
    access = client.cookies.get("adp_at")
    assert access

    client.cookies.clear()
    client.cookies.set("adp_at", access, path="/api")
    assert (await client.get("/api/v1/users")).status_code == 403

    login_as(client, rd_admin)
    promoted = await client.patch(f"/api/v1/users/{target.id}", json={"role": "org_admin"})
    assert promoted.status_code == 200

    client.cookies.clear()
    client.cookies.set("adp_at", access, path="/api")
    assert (await client.get("/api/v1/users")).status_code == 200


async def test_reset_password(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    target = await make_user(session, Role.builder, org_tree.platform, username="resetuser")

    login = await client.post(
        "/api/v1/auth/login", json={"username": "resetuser", "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200
    refresh = client.cookies.get("adp_rt")
    assert refresh

    login_as(client, rd_admin)
    reset = await client.post(
        f"/api/v1/users/{target.id}:reset-password", json={"new_password": "NewPass@123"}
    )
    assert reset.status_code == 204

    # 原刷新令牌失效
    client.cookies.clear()
    client.cookies.set("adp_rt", refresh, path="/api/v1/auth/refresh")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401

    # 用新密码登录，仍要求改密
    client.cookies.clear()
    relogin = await client.post(
        "/api/v1/auth/login", json={"username": "resetuser", "password": "NewPass@123"}
    )
    assert relogin.status_code == 200
    assert relogin.json()["must_change_password"] is True


async def test_locked_flag_and_unlock(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    target = await make_user(session, Role.builder, org_tree.platform, username="lockeduser")

    for _ in range(5):
        await client.post(
            "/api/v1/auth/login",
            json={"username": "lockeduser", "password": "wrong-password"},
            headers={"X-Real-IP": "10.9.9.9"},
        )

    login_as(client, rd_admin)
    listing = await client.get("/api/v1/users", params={"keyword": "lockeduser"})
    assert listing.json()["items"][0]["locked"] is True

    unlock = await client.post(f"/api/v1/users/{target.id}:unlock")
    assert unlock.status_code == 204

    listing = await client.get("/api/v1/users", params={"keyword": "lockeduser"})
    assert listing.json()["items"][0]["locked"] is False

    ok = await client.post(
        "/api/v1/auth/login",
        json={"username": "lockeduser", "password": DEFAULT_PASSWORD},
        headers={"X-Real-IP": "10.9.9.9"},
    )
    assert ok.status_code == 200


async def test_cannot_manage_self(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    login_as(client, rd_admin)

    assert (
        await client.patch(f"/api/v1/users/{rd_admin.id}", json={"name": "x"})
    ).status_code == 403
    assert (await client.post(f"/api/v1/users/{rd_admin.id}:disable")).status_code == 403
    assert (await client.post(f"/api/v1/users/{rd_admin.id}:unlock")).status_code == 403


async def test_list_org_scope_and_hidden_sysadmin(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    sys_admin = await make_user(session, Role.sys_admin, username="sysadmin")
    rd_admin = await _rd_admin(session, org_tree)
    await make_user(session, Role.builder, org_tree.group, username="group_builder")
    await make_user(session, Role.builder, org_tree.rd, username="rd_builder")
    await make_user(session, Role.builder, org_tree.platform, username="plat_builder")
    await make_user(session, Role.builder, org_tree.product, username="product_builder")

    login_as(client, rd_admin)
    all_rows = await client.get("/api/v1/users")
    assert {row["username"] for row in all_rows.json()["items"]} == {
        "rdadmin",
        "rd_builder",
        "plat_builder",
    }

    with_sub = await client.get(
        "/api/v1/users", params={"org_id": org_tree.rd.id, "include_sub": True}
    )
    assert {row["username"] for row in with_sub.json()["items"]} == {
        "rdadmin",
        "rd_builder",
        "plat_builder",
    }

    direct = await client.get(
        "/api/v1/users", params={"org_id": org_tree.platform.id, "include_sub": False}
    )
    assert {row["username"] for row in direct.json()["items"]} == {"plat_builder"}

    denied = await client.get("/api/v1/users", params={"org_id": org_tree.product.id})
    assert denied.status_code == 403

    login_as(client, sys_admin)
    sys_rows = await client.get("/api/v1/users")
    assert {row["username"] for row in sys_rows.json()["items"]} == {
        "sysadmin",
        "rdadmin",
        "group_builder",
        "rd_builder",
        "plat_builder",
        "product_builder",
    }


async def test_keyword_like_escaped(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    await make_user(session, Role.builder, org_tree.rd, username="pct_user")
    await make_user(session, Role.builder, org_tree.rd, username="pctxuser")

    login_as(client, rd_admin)
    literal = await client.get("/api/v1/users", params={"keyword": "pct_use"})
    assert {row["username"] for row in literal.json()["items"]} == {"pct_user"}

    wildcard = await client.get("/api/v1/users", params={"keyword": "pct%user"})
    assert wildcard.json()["items"] == []


async def test_audits(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    rd_admin = await _rd_admin(session, org_tree)
    login_as(client, rd_admin)

    created = await client.post(
        "/api/v1/users",
        json={
            "username": "audituser",
            "password": "Secret@123",
            "name": "审计用户",
            "org_id": org_tree.platform.id,
            "role": "builder",
        },
    )
    user_id = created.json()["id"]
    await client.patch(f"/api/v1/users/{user_id}", json={"name": "改名"})
    await client.patch(f"/api/v1/users/{user_id}", json={"role": "org_admin"})
    await client.patch(f"/api/v1/users/{user_id}", json={"org_id": org_tree.algo.id})
    await client.post(f"/api/v1/users/{user_id}:disable")
    await client.post(f"/api/v1/users/{user_id}:enable")
    await client.post(
        f"/api/v1/users/{user_id}:reset-password", json={"new_password": "NewPass@123"}
    )
    await client.post(f"/api/v1/users/{user_id}:unlock")

    logs = (
        (
            await session.execute(
                select(AuditLog).where(AuditLog.target_id == user_id).order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )
    actions = {log.action for log in logs}
    assert {
        "user.create",
        "user.update",
        "user.change_role",
        "user.change_org",
        "user.disable",
        "user.enable",
        "user.reset_password",
        "user.unlock",
    } <= actions

    role_log = next(log for log in logs if log.action == "user.change_role")
    assert role_log.detail == {"from": "builder", "to": "org_admin"}
    org_log = next(log for log in logs if log.action == "user.change_org")
    assert org_log.detail["from"]["id"] == org_tree.platform.id
    assert org_log.detail["to"]["id"] == org_tree.algo.id
    # 密码不写进审计
    assert "NewPass@123" not in json.dumps([log.detail for log in logs])


async def test_user_search(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    builder = await make_user(session, Role.builder, org_tree.rd, username="searcher")
    end_user = await make_user(session, Role.end_user, org_tree.rd, username="enduser")
    for index in range(25):
        await make_user(session, Role.builder, org_tree.rd, username=f"match{index:02d}")
    await make_user(session, Role.builder, org_tree.rd, username="disabledmatch", status="disabled")
    named = await make_user(session, Role.builder, org_tree.rd, username="xyz")
    named.name = "关键字用户"
    await session.commit()

    login_as(client, builder)
    response = await client.get("/api/v1/users:search", params={"q": "match"})
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 20
    assert all(row["username"].startswith("match") for row in rows)
    assert "disabledmatch" not in {row["username"] for row in rows}

    by_name = await client.get("/api/v1/users:search", params={"q": "关键字"})
    assert [row["username"] for row in by_name.json()] == ["xyz"]
    assert by_name.json()[0]["org"] == {"id": org_tree.rd.id, "name": org_tree.rd.name}

    # 停用用户不出现在搜索结果里
    disabled = await client.get("/api/v1/users:search", params={"q": "disabledmatch"})
    assert disabled.json() == []

    login_as(client, end_user)
    denied = await client.get("/api/v1/users:search", params={"q": "match"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_DENIED"
