"""请求上下文与权限依赖测试。"""

import uuid
from typing import Annotated

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient

from app.core.schemas import Role
from app.core.security import create_access_token
from app.iam.context import Ctx, bump_perm_version, current_ctx
from app.iam.deps import require_perm
from app.iam.permissions import ALL_PERMISSIONS, ROLE_PERMISSIONS
from app.main import create_app
from tests.factories import auth_cookies, make_org, make_user

probe_router = APIRouter()


@probe_router.get("/test/ctx")
async def read_test_ctx(ctx: Annotated[Ctx, Depends(current_ctx)]) -> dict:
    return {
        "user_id": ctx.user_id,
        "role": ctx.role.value,
        "ip": ctx.ip,
        "pwc": ctx.pwc,
        "perms": sorted(ctx.perms),
    }


@probe_router.get("/test/normal")
async def normal(ctx: Annotated[Ctx, Depends(current_ctx)]) -> dict:
    return {"ok": True}


def _perm_endpoint(code: str):
    async def endpoint(ctx: Annotated[Ctx, Depends(require_perm(code))]) -> dict:
        return {"ok": True}

    return endpoint


for _code in ALL_PERMISSIONS:
    probe_router.add_api_route(f"/test/perm/{_code}", _perm_endpoint(_code), methods=["GET"])


@pytest_asyncio.fixture(scope="session")
async def ctx_client(settings):
    application = create_app(settings)
    application.include_router(probe_router)
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await application.state.redis.aclose()
    await application.state.engine.dispose()


async def test_missing_cookie(ctx_client: AsyncClient) -> None:
    response = await ctx_client.get("/test/ctx")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


async def test_bad_signature(ctx_client: AsyncClient) -> None:
    response = await ctx_client.get("/test/ctx", cookies={"adp_at": "not-a-token"})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


async def test_expired_token(ctx_client: AsyncClient, session) -> None:
    org = await make_org(session, "研发中心")
    user = await make_user(session, role=Role.builder, org=org)
    token = create_access_token(user.id, "s", user.token_version, False, -1)
    response = await ctx_client.get("/test/ctx", cookies={"adp_at": token})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


async def test_disabled_user_rejected_immediately(ctx_client: AsyncClient, session, redis) -> None:
    org = await make_org(session, "研发中心")
    user = await make_user(session, role=Role.builder, org=org)
    cookies = auth_cookies(user)
    assert (await ctx_client.get("/test/ctx", cookies=cookies)).status_code == 200

    user.status = "disabled"
    await session.commit()
    await bump_perm_version(redis)

    response = await ctx_client.get("/test/ctx", cookies=cookies)
    assert response.status_code == 401


async def test_role_change_applies_immediately(ctx_client: AsyncClient, session, redis) -> None:
    org = await make_org(session, "研发中心")
    user = await make_user(session, role=Role.builder, org=org)
    cookies = auth_cookies(user)

    denied = await ctx_client.get("/test/perm/user.manage", cookies=cookies)
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_DENIED"

    user.role = Role.org_admin
    await session.commit()
    await bump_perm_version(redis)

    allowed = await ctx_client.get("/test/perm/user.manage", cookies=cookies)
    assert allowed.status_code == 200


async def test_pwc_rejected_on_normal_but_allowed_on_me(ctx_client: AsyncClient, session) -> None:
    user = await make_user(session, role=Role.sys_admin)
    cookies = auth_cookies(user, pwc=True)

    response = await ctx_client.get("/test/normal", cookies=cookies)
    assert response.status_code == 403
    assert response.json()["code"] == "PASSWORD_CHANGE_REQUIRED"

    me = await ctx_client.get("/api/v1/me", cookies=cookies)
    assert me.status_code == 200
    assert me.json()["must_change_password"] is True


async def test_real_ip_used_and_forwarded_for_ignored(ctx_client: AsyncClient, session) -> None:
    user = await make_user(session, role=Role.sys_admin)
    cookies = auth_cookies(user)

    response = await ctx_client.get(
        "/test/ctx",
        cookies=cookies,
        headers={"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "9.9.9.9"},
    )
    assert response.json()["ip"] == "1.2.3.4"

    fallback = await ctx_client.get(
        "/test/ctx", cookies=cookies, headers={"X-Forwarded-For": "9.9.9.9"}
    )
    assert fallback.json()["ip"] != "9.9.9.9"


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("code", sorted(ALL_PERMISSIONS))
async def test_require_perm_matrix(ctx_client: AsyncClient, session, role: Role, code: str) -> None:
    org = None if role == Role.sys_admin else await make_org(session, f"o-{uuid.uuid4().hex[:8]}")
    user = await make_user(session, role=role, org=org)

    response = await ctx_client.get(f"/test/perm/{code}", cookies=auth_cookies(user))
    expected = 200 if code in ROLE_PERMISSIONS[role] else 403
    assert response.status_code == expected
