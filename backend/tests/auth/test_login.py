"""登录、失败锁定与审计测试。"""

from httpx import AsyncClient
from sqlalchemy import select

from app.audit.tables import AuditLog
from app.auth import lockout
from app.core.schemas import Role
from app.settings import service as settings_service
from tests.factories import DEFAULT_PASSWORD, make_org, make_user


async def _login(client: AsyncClient, username: str, password: str, ip: str):
    return await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-Real-IP": ip},
    )


async def test_lock_after_five_failures(client: AsyncClient, session) -> None:
    org = await make_org(session, "锁定组织")
    await make_user(session, Role.builder, org=org, username="alice")
    ip = "10.0.0.1"

    for _ in range(5):
        response = await _login(client, "alice", "wrong-password", ip)
        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_CREDENTIALS"

    locked = await _login(client, "alice", DEFAULT_PASSWORD, ip)
    assert locked.status_code == 429
    assert locked.json()["code"] == "LOGIN_LOCKED"

    # 其他 IP 不受锁定影响
    other = await _login(client, "alice", DEFAULT_PASSWORD, "10.0.0.2")
    assert other.status_code == 200


async def test_username_case_and_spaces_share_lock(client: AsyncClient, session) -> None:
    org = await make_org(session, "大小写组织")
    await make_user(session, Role.builder, org=org, username="bob")
    ip = "10.0.0.3"

    for _ in range(3):
        await _login(client, "bob", "bad-password", ip)
    for _ in range(2):
        await _login(client, "  BOB  ", "bad-password", ip)

    locked = await _login(client, " BOB ", DEFAULT_PASSWORD, ip)
    assert locked.status_code == 429
    assert locked.json()["code"] == "LOGIN_LOCKED"


async def test_forwarded_for_ignored(client: AsyncClient, session) -> None:
    org = await make_org(session, "伪造头组织")
    await make_user(session, Role.builder, org=org, username="carol")
    ip = "10.0.0.4"

    for i in range(5):
        await client.post(
            "/api/v1/auth/login",
            json={"username": "carol", "password": "bad-password"},
            headers={"X-Real-IP": ip, "X-Forwarded-For": f"1.2.3.{i}"},
        )

    locked = await client.post(
        "/api/v1/auth/login",
        json={"username": "carol", "password": DEFAULT_PASSWORD},
        headers={"X-Real-IP": ip, "X-Forwarded-For": "9.9.9.9"},
    )
    assert locked.status_code == 429


async def test_lock_ttl_and_unlock(client: AsyncClient, session, redis) -> None:
    org = await make_org(session, "解锁组织")
    await make_user(session, Role.builder, org=org, username="dave")
    await settings_service.update_settings(session, {"login": {"lock_minutes": 1}}, actor_id=0)
    ip = "10.0.0.5"

    for _ in range(5):
        await _login(client, "dave", "bad-password", ip)

    assert 0 < await redis.ttl("login_lock:dave:10.0.0.5") <= 60
    assert 0 < await redis.ttl("login_fail:dave:10.0.0.5") <= 60
    assert 0 < await redis.ttl("login_locked:dave") <= 60
    assert (await _login(client, "dave", DEFAULT_PASSWORD, ip)).status_code == 429

    await lockout.unlock(redis, "dave")
    assert await redis.exists("login_lock:dave:10.0.0.5") == 0
    assert await redis.exists("login_fail:dave:10.0.0.5") == 0
    assert await lockout.is_locked(redis, "dave") is False

    unlocked = await _login(client, "dave", DEFAULT_PASSWORD, ip)
    assert unlocked.status_code == 200


async def test_nonexistent_and_wrong_password_same(client: AsyncClient, session) -> None:
    org = await make_org(session, "统一提示组织")
    await make_user(session, Role.builder, org=org, username="erin")

    missing = await _login(client, "ghost", "whatever", "10.0.0.6")
    wrong = await _login(client, "erin", "whatever", "10.0.0.6")

    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["code"] == wrong.json()["code"] == "INVALID_CREDENTIALS"
    assert missing.json()["message"] == wrong.json()["message"]


async def test_disabled_user(client: AsyncClient, session) -> None:
    org = await make_org(session, "停用组织")
    await make_user(session, Role.builder, org=org, username="frank", status="disabled")

    correct = await _login(client, "frank", DEFAULT_PASSWORD, "10.0.0.7")
    assert correct.status_code == 403
    assert correct.json()["code"] == "ACCOUNT_DISABLED"

    wrong = await _login(client, "frank", "wrong-password", "10.0.0.7")
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "INVALID_CREDENTIALS"


async def test_login_cookies_and_must_change_password(client: AsyncClient, session) -> None:
    org = await make_org(session, "Cookie 组织")
    await make_user(session, Role.builder, org=org, username="grace", must_change_password=True)

    response = await _login(client, "grace", DEFAULT_PASSWORD, "10.0.0.8")
    assert response.status_code == 200
    assert response.json()["must_change_password"] is True

    cookies = response.headers.get_list("set-cookie")
    access = next(c for c in cookies if c.startswith("adp_at="))
    refresh = next(c for c in cookies if c.startswith("adp_rt="))

    assert "Path=/api" in access
    assert "HttpOnly" in access
    assert "samesite=strict" in access.lower()
    assert "Max-Age=900" in access
    assert "Path=/api/v1/auth/refresh" in refresh
    assert "HttpOnly" in refresh
    assert "Max-Age=604800" in refresh


async def test_lock_threshold_from_settings(client: AsyncClient, session) -> None:
    org = await make_org(session, "阈值组织")
    await make_user(session, Role.builder, org=org, username="heidi")
    await settings_service.update_settings(session, {"login": {"lock_threshold": 3}}, actor_id=0)
    ip = "10.0.0.9"

    for _ in range(3):
        response = await _login(client, "heidi", "bad-password", ip)
        assert response.status_code == 401

    locked = await _login(client, "heidi", DEFAULT_PASSWORD, ip)
    assert locked.status_code == 429


async def test_login_writes_audit(client: AsyncClient, session) -> None:
    org = await make_org(session, "审计组织")
    user = await make_user(session, Role.builder, org=org, username="ivan")
    ip = "10.0.0.10"

    await _login(client, "ivan", "bad-password", ip)
    await _login(client, "ivan", DEFAULT_PASSWORD, ip)

    rows = list(
        (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "auth.login").order_by(AuditLog.id)
            )
        ).all()
    )
    assert len(rows) == 2

    failure, success = rows
    assert failure.result == "failure"
    assert failure.detail == {"reason": "wrong_password"}
    assert failure.actor_id == user.id
    assert failure.actor_name == "ivan"
    assert failure.ip == ip

    assert success.result == "success"
    assert success.actor_id == user.id
    assert success.actor_name == "ivan"
    assert success.ip == ip
