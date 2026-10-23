"""系统设置接口测试。"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.iam.tables import User
from tests.factories import DEFAULT_PASSWORD, auth_cookies, make_user

DEFAULT_SETTINGS = {
    "password": {"min_length": 8, "min_classes": 3},
    "login": {"lock_threshold": 5, "lock_minutes": 15},
    "session": {"access_minutes": 15, "refresh_days": 7},
    "model_call": {"ingest_rate_ratio": 0.5, "model_host_allowlist": []},
}


def login_as(client: AsyncClient, user: User) -> None:
    """在 client 实例上设 Cookie，供后续请求使用。"""
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


@pytest_asyncio.fixture
async def sys_admin(session: AsyncSession) -> User:
    return await make_user(session, Role.sys_admin, username="settings-admin")


@pytest.mark.parametrize("role", [Role.org_admin, Role.builder, Role.end_user])
async def test_non_sys_admin_forbidden(
    client: AsyncClient, session: AsyncSession, org_tree, role: Role
) -> None:
    user = await make_user(session, role, org_tree.rd, username=f"u-{role.value}")
    login_as(client, user)

    got = await client.get("/api/v1/settings")
    assert got.status_code == 403
    assert got.json()["code"] == "PERMISSION_DENIED"

    patched = await client.patch("/api/v1/settings", json={"login": {"lock_threshold": 6}})
    assert patched.status_code == 403
    assert patched.json()["code"] == "PERMISSION_DENIED"

    # GET 被拒不写审计；PATCH 被拒写一条 failure。
    rows = list(
        (
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "settings.update", AuditLog.result == "failure"
                )
            )
        ).all()
    )
    assert len(rows) == 1


async def test_sys_admin_get_defaults(
    client: AsyncClient, session: AsyncSession, sys_admin: User
) -> None:
    login_as(client, sys_admin)
    response = await client.get("/api/v1/settings")
    assert response.status_code == 200
    assert response.json() == DEFAULT_SETTINGS


async def test_patch_single_field_keeps_others(
    client: AsyncClient, session: AsyncSession, sys_admin: User
) -> None:
    login_as(client, sys_admin)
    response = await client.patch("/api/v1/settings", json={"login": {"lock_threshold": 6}})
    assert response.status_code == 200

    body = response.json()
    assert body["login"]["lock_threshold"] == 6
    assert body["login"]["lock_minutes"] == 15
    assert body["password"] == DEFAULT_SETTINGS["password"]
    assert body["session"] == DEFAULT_SETTINGS["session"]
    assert body["model_call"] == DEFAULT_SETTINGS["model_call"]

    # 再读一次确认已落库。
    again = await client.get("/api/v1/settings")
    assert again.status_code == 200
    assert again.json() == body


async def test_out_of_range_422(
    client: AsyncClient, session: AsyncSession, sys_admin: User
) -> None:
    login_as(client, sys_admin)
    response = await client.patch("/api/v1/settings", json={"password": {"min_length": 4}})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert any("min_length" in err["loc"] for err in body["detail"]["errors"])


async def test_bad_allowlist_422(
    client: AsyncClient, session: AsyncSession, sys_admin: User
) -> None:
    login_as(client, sys_admin)
    response = await client.patch(
        "/api/v1/settings", json={"model_call": {"model_host_allowlist": ["not a host!"]}}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert any("model_host_allowlist" in err["loc"] for err in body["detail"]["errors"])


async def test_audit_detail_only_changes(
    client: AsyncClient, session: AsyncSession, sys_admin: User
) -> None:
    login_as(client, sys_admin)
    response = await client.patch(
        "/api/v1/settings",
        json={
            "login": {"lock_threshold": 6, "lock_minutes": 30},
            "session": {"refresh_days": 7},
        },
    )
    assert response.status_code == 200

    rows = list(
        (await session.scalars(select(AuditLog).where(AuditLog.action == "settings.update"))).all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.result == "success"
    assert row.target_type == "settings"
    assert row.target_desc == "系统设置"
    assert row.target_org_id is None
    # refresh_days 传了默认值，不算变化；detail 只含真正变化的字段。
    assert row.detail == {
        "changes": {
            "login.lock_threshold": [5, 6],
            "login.lock_minutes": [15, 30],
        }
    }


async def test_no_change_no_audit(
    client: AsyncClient, session: AsyncSession, sys_admin: User
) -> None:
    login_as(client, sys_admin)
    response = await client.patch("/api/v1/settings", json={"login": {"lock_threshold": 5}})
    assert response.status_code == 200

    rows = list(
        (await session.scalars(select(AuditLog).where(AuditLog.action == "settings.update"))).all()
    )
    assert rows == []


async def test_lock_threshold_change_affects_login(
    client: AsyncClient, session: AsyncSession, org_tree, sys_admin: User
) -> None:
    await make_user(session, Role.builder, org_tree.rd, username="lock-target")

    login_as(client, sys_admin)
    patched = await client.patch("/api/v1/settings", json={"login": {"lock_threshold": 3}})
    assert patched.status_code == 200

    client.cookies.clear()
    ip = "10.9.9.9"
    for _ in range(3):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "lock-target", "password": "wrong-password"},
            headers={"X-Real-IP": ip},
        )
        assert response.status_code == 401

    locked = await client.post(
        "/api/v1/auth/login",
        json={"username": "lock-target", "password": DEFAULT_PASSWORD},
        headers={"X-Real-IP": ip},
    )
    assert locked.status_code == 429
    assert locked.json()["code"] == "LOGIN_LOCKED"
