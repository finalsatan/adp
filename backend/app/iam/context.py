"""请求上下文：从访问令牌加载当前用户、角色、组织和权限码。"""

import json
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import user_id_var
from app.core.db import get_session
from app.core.errors import AuthRequiredError, PasswordChangeRequiredError
from app.core.redis import get_redis
from app.core.schemas import Role
from app.core.security import TokenInvalid, decode_access_token
from app.iam.permissions import ROLE_PERMISSIONS
from app.iam.tables import Org, User

ACCESS_COOKIE = "adp_at"
PERM_VERSION_KEY = "perm_version"
CTX_CACHE_TTL_SECONDS = 600


@dataclass(frozen=True)
class Ctx:
    """一次请求的当前用户上下文（不可变）。"""

    user_id: int
    username: str
    name: str
    role: Role
    status: str
    org_id: int | None
    org_path: str | None
    org_name: str | None
    perms: frozenset[str]
    session_id: str
    pwc: bool
    ip: str | None
    request_id: str | None


def client_ip(request: Request) -> str | None:
    """来源 IP 只取 nginx 写入的 X-Real-IP，不信任 X-Forwarded-For。"""
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    return request.client.host if request.client else None


async def bump_perm_version(redis: Redis) -> None:
    """把全局权限版本加 1，让所有人的上下文缓存立即失效。"""
    await redis.incr(PERM_VERSION_KEY)


async def _load_snapshot(session: AsyncSession, user_id: int) -> dict | None:
    result = await session.execute(
        select(
            User.username.label("username"),
            User.name.label("name"),
            User.role.label("role"),
            User.status.label("status"),
            User.org_id.label("org_id"),
            Org.path.label("org_path"),
            Org.name.label("org_name"),
        )
        .select_from(User)
        .join(Org, User.org_id == Org.id, isouter=True)
        .where(User.id == user_id)
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def _get_snapshot(session: AsyncSession, redis: Redis, user_id: int) -> dict | None:
    perm_version = await redis.get(PERM_VERSION_KEY) or "0"
    cache_key = f"user_ctx:{user_id}:{perm_version}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    snapshot = await _load_snapshot(session, user_id)
    if snapshot is not None:
        await redis.set(
            cache_key, json.dumps(snapshot, ensure_ascii=False), ex=CTX_CACHE_TTL_SECONDS
        )
    return snapshot


async def _build_ctx(
    request: Request,
    session: AsyncSession,
    redis: Redis,
    allow_pwc: bool,
) -> Ctx:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise AuthRequiredError()
    try:
        claims = decode_access_token(token)
    except TokenInvalid:
        raise AuthRequiredError() from None

    snapshot = await _get_snapshot(session, redis, claims.user_id)
    if snapshot is None or snapshot["status"] != "active":
        raise AuthRequiredError()

    role = Role(snapshot["role"])
    ctx = Ctx(
        user_id=claims.user_id,
        username=snapshot["username"],
        name=snapshot["name"],
        role=role,
        status=snapshot["status"],
        org_id=snapshot["org_id"],
        org_path=snapshot["org_path"],
        org_name=snapshot["org_name"],
        perms=ROLE_PERMISSIONS.get(role, frozenset()),
        session_id=claims.sid,
        pwc=claims.pwc,
        ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )

    request.state.ctx = ctx
    user_id_var.set(ctx.user_id)

    if ctx.pwc and not allow_pwc:
        raise PasswordChangeRequiredError()
    return ctx


async def current_ctx(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Ctx:
    """拒绝待改密用户的请求上下文。"""
    return await _build_ctx(request, session, redis, allow_pwc=False)


async def current_ctx_allow_pwc(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Ctx:
    """允许待改密用户的请求上下文（改密、登出、/me、/auth/policy 用）。"""
    return await _build_ctx(request, session, redis, allow_pwc=True)
