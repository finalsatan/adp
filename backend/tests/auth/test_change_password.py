"""改密测试：待改密限制、原密码校验、弱密码、会话作废。"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.audit.tables import AuditLog
from app.auth.tables import RefreshToken
from app.core.schemas import Role
from app.core.security import hash_refresh_token, new_refresh_token
from tests.factories import DEFAULT_PASSWORD, auth_cookies, make_org, make_user


def _login_as(client: AsyncClient, user, pwc: bool = False) -> None:
    client.cookies.set("adp_at", auth_cookies(user, pwc=pwc)["adp_at"], path="/api")


async def test_must_change_password_flow(client: AsyncClient, session) -> None:
    org = await make_org(session, "待改密组织")
    user = await make_user(
        session, Role.builder, org=org, username="pwcuser", must_change_password=True
    )
    _login_as(client, user, pwc=True)

    # 待改密时访问普通接口被拒
    blocked = await client.get("/api/v1/resource-types")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"

    # 待改密时不要求原密码
    changed = await client.post(
        "/api/v1/auth/change-password", json={"new_password": "NewPass123!"}
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False

    # 响应里的新 Cookie 立即能访问普通接口
    me = await client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["must_change_password"] is False


async def test_missing_old_password_returns_422(client: AsyncClient, session) -> None:
    org = await make_org(session, "缺原密码组织")
    user = await make_user(session, Role.builder, org=org, username="normaluser")
    _login_as(client, user)

    response = await client.post(
        "/api/v1/auth/change-password", json={"new_password": "NewPass123!"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_wrong_old_password_returns_400(client: AsyncClient, session) -> None:
    org = await make_org(session, "原密码错误组织")
    user = await make_user(session, Role.builder, org=org, username="normaluser")
    _login_as(client, user)

    response = await client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "wrong-password", "new_password": "NewPass123!"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "WRONG_OLD_PASSWORD"


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("Ab1!", "TOO_SHORT"),
        ("abcdefgh", "TOO_FEW_CLASSES"),
        ("weakuser", "SAME_AS_USERNAME"),
        ("Password1", "COMMON_PASSWORD"),
    ],
)
async def test_weak_password_reasons(
    client: AsyncClient, session, password: str, reason: str
) -> None:
    org = await make_org(session, "弱密码组织")
    user = await make_user(session, Role.builder, org=org, username="weakuser")
    _login_as(client, user)

    response = await client.post(
        "/api/v1/auth/change-password",
        json={"old_password": DEFAULT_PASSWORD, "new_password": password},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "WEAK_PASSWORD"
    assert reason in response.json()["detail"]["reasons"]


async def test_change_password_revokes_other_sessions(client: AsyncClient, session) -> None:
    org = await make_org(session, "多会话组织")
    user = await make_user(session, Role.builder, org=org, username="multisession")

    await client.post(
        "/api/v1/auth/login",
        json={"username": "multisession", "password": DEFAULT_PASSWORD},
        headers={"X-Real-IP": "10.2.0.1"},
    )
    old_raw = client.cookies.get("adp_rt")

    # 另一个会话的刷新令牌直接写库
    other_raw, _ = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            session_id="session-b",
            token_hash=hash_refresh_token(other_raw),
            token_version=user.token_version,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    await session.commit()

    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"old_password": DEFAULT_PASSWORD, "new_password": "NewPass123!"},
    )
    assert changed.status_code == 200
    new_raw = changed.cookies.get("adp_rt")
    assert new_raw and new_raw != old_raw

    other_row = await session.scalar(
        select(RefreshToken).where(RefreshToken.session_id == "session-b")
    )
    await session.refresh(other_row)
    assert other_row.revoked_at is not None

    old_row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(old_raw))
    )
    await session.refresh(old_row)
    assert old_row.revoked_at is not None

    # 当前会话的新刷新令牌可用
    client.cookies.clear()
    client.cookies.set("adp_rt", new_raw, path="/api/v1/auth/refresh")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


async def test_change_password_writes_audit(client: AsyncClient, session) -> None:
    org = await make_org(session, "改密审计组织")
    user = await make_user(session, Role.builder, org=org, username="audituser")
    _login_as(client, user)

    response = await client.post(
        "/api/v1/auth/change-password",
        json={"old_password": DEFAULT_PASSWORD, "new_password": "NewPass123!"},
    )
    assert response.status_code == 200

    rows = list(
        (
            await session.scalars(select(AuditLog).where(AuditLog.action == "auth.change_password"))
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].result == "success"
    assert rows[0].actor_id == user.id
    assert rows[0].target_type == "user"
    assert rows[0].target_id == user.id
