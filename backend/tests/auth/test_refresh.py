"""刷新令牌的轮换、宽限期、盗用检测与登出测试。"""

import asyncio
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select

from app.auth import service
from app.auth.tables import RefreshToken
from app.core.schemas import Role
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_refresh_token,
)
from tests.factories import DEFAULT_PASSWORD, make_org, make_user


def _set_cookie(client: AsyncClient, name: str, value: str, path: str = "/") -> None:
    client.cookies.clear()
    client.cookies.set(name, value, path=path)


async def _login(client: AsyncClient, username: str = "refreshuser", ip: str = "10.1.0.1"):
    return await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": DEFAULT_PASSWORD},
        headers={"X-Real-IP": ip},
    )


async def test_refresh_rotation(client: AsyncClient, session) -> None:
    org = await make_org(session, "轮换组织")
    await make_user(session, Role.builder, org=org, username="refreshuser")
    await _login(client)
    old_raw = client.cookies.get("adp_rt")
    assert old_raw

    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    new_raw = first.cookies.get("adp_rt")
    assert new_raw and new_raw != old_raw

    # 新令牌可用，并且再次轮换
    _set_cookie(client, "adp_rt", new_raw, path="/api/v1/auth/refresh")
    second = await client.post("/api/v1/auth/refresh")
    assert second.status_code == 200
    assert second.cookies.get("adp_rt") != new_raw


async def test_refresh_grace_returns_same_token(client: AsyncClient, session) -> None:
    org = await make_org(session, "宽限组织")
    await make_user(session, Role.builder, org=org, username="refreshuser")
    await _login(client)
    old_raw = client.cookies.get("adp_rt")

    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    new_raw = first.cookies.get("adp_rt")

    # 宽限期内用旧令牌刷新，拿到的还是第一次签发的新令牌
    _set_cookie(client, "adp_rt", old_raw, path="/api/v1/auth/refresh")
    grace = await client.post("/api/v1/auth/refresh")
    assert grace.status_code == 200
    assert grace.cookies.get("adp_rt") == new_raw


async def test_refresh_beyond_grace_revokes_session(client: AsyncClient, session, redis) -> None:
    org = await make_org(session, "盗用组织")
    await make_user(session, Role.builder, org=org, username="refreshuser")
    await _login(client)
    old_raw = client.cookies.get("adp_rt")

    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    new_raw = first.cookies.get("adp_rt")

    # 删掉宽限期缓存，模拟超过 30 秒
    await redis.delete(service.grace_key(hash_refresh_token(old_raw)))
    _set_cookie(client, "adp_rt", old_raw, path="/api/v1/auth/refresh")
    reused = await client.post("/api/v1/auth/refresh")
    assert reused.status_code == 401
    assert reused.json()["code"] == "REFRESH_INVALID"
    # 失败响应要清除两个 Cookie
    cleared = reused.headers.get_list("set-cookie")
    assert any(c.startswith("adp_at=") and "Max-Age=0" in c for c in cleared)
    assert any(c.startswith("adp_rt=") and "Max-Age=0" in c for c in cleared)

    # 该 session 的全部刷新令牌都被作废，包括第一次签发的新令牌
    _set_cookie(client, "adp_rt", new_raw, path="/api/v1/auth/refresh")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_concurrent_refresh_leaves_one_active_token(client: AsyncClient, session) -> None:
    org = await make_org(session, "并发组织")
    await make_user(session, Role.builder, org=org, username="refreshuser")
    await _login(client)
    claims = decode_access_token(client.cookies.get("adp_at"))

    responses = await asyncio.gather(
        client.post("/api/v1/auth/refresh"),
        client.post("/api/v1/auth/refresh"),
    )
    assert all(response.status_code == 200 for response in responses)
    # 两个响应拿到的应是同一个新令牌
    tokens = {response.cookies.get("adp_rt") for response in responses}
    assert len(tokens) == 1

    active = await session.scalar(
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.session_id == claims.sid, RefreshToken.revoked_at.is_(None))
    )
    assert active == 1


async def test_refresh_disabled_user(client: AsyncClient, session) -> None:
    org = await make_org(session, "停用刷新组织")
    user = await make_user(session, Role.builder, org=org, username="refreshuser")
    await _login(client)

    user.status = "disabled"
    await session.commit()

    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["code"] == "REFRESH_INVALID"


async def test_refresh_token_version_changed(client: AsyncClient, session) -> None:
    org = await make_org(session, "版本组织")
    user = await make_user(session, Role.builder, org=org, username="refreshuser")
    await _login(client)

    user.token_version += 1
    await session.commit()

    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["code"] == "REFRESH_INVALID"


async def test_refresh_after_logout(client: AsyncClient, session) -> None:
    org = await make_org(session, "登出组织")
    await make_user(session, Role.builder, org=org, username="refreshuser")
    await _login(client)
    raw = client.cookies.get("adp_rt")

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204

    _set_cookie(client, "adp_rt", raw, path="/api/v1/auth/refresh")
    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["code"] == "REFRESH_INVALID"


async def test_logout_with_expired_access_token(client: AsyncClient, session) -> None:
    org = await make_org(session, "过期登出组织")
    await make_user(session, Role.builder, org=org, username="refreshuser")
    await _login(client)
    raw = client.cookies.get("adp_rt")
    claims = decode_access_token(client.cookies.get("adp_at"))

    expired = create_access_token(claims.user_id, claims.sid, claims.tv, claims.pwc, ttl_minutes=-1)
    _set_cookie(client, "adp_at", expired, path="/api")

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204

    _set_cookie(client, "adp_rt", raw, path="/api/v1/auth/refresh")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_refresh_expired_token(client: AsyncClient, session) -> None:
    org = await make_org(session, "过期令牌组织")
    await make_user(session, Role.builder, org=org, username="refreshuser")
    await _login(client)
    raw = client.cookies.get("adp_rt")

    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw))
    )
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["code"] == "REFRESH_INVALID"
