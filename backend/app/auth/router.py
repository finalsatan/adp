"""认证接口：登录、刷新、登出、改密、密码策略。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.deps import audit_success, audited
from app.audit.service import AuditActor, AuditTarget, record
from app.auth import service
from app.auth.cookies import REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from app.auth.schemas import AuthPolicyOut, ChangePasswordIn, LoginIn, MustChangePasswordOut
from app.core.db import get_session
from app.core.redis import get_redis
from app.core.schemas import AuditResult
from app.iam.context import ACCESS_COOKIE, Ctx, client_ip, current_ctx_allow_pwc
from app.settings import service as settings_service

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])


@router.post("/login", response_model=MustChangePasswordOut, summary="登录")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> MustChangePasswordOut:
    username = body.username.strip().lower()
    ip = client_ip(request)
    outcome = await service.login(
        session, redis, username, body.password, ip, request.headers.get("user-agent")
    )
    actor = AuditActor(
        id=outcome.user.id if outcome.user is not None else None,
        name=outcome.user.username if outcome.user is not None else username,
        org_id=outcome.user.org_id if outcome.user is not None else None,
        ip=ip,
    )
    if outcome.error is not None:
        await record(
            None,
            "auth.login",
            None,
            AuditResult.failure,
            {"reason": outcome.reason},
            actor=actor,
        )
        raise outcome.error

    assert outcome.tokens is not None
    await record(None, "auth.login", None, AuditResult.success, actor=actor)
    set_auth_cookies(
        response,
        outcome.tokens.access_token,
        outcome.tokens.refresh_token,
        outcome.tokens.access_minutes,
        outcome.tokens.refresh_days,
    )
    return MustChangePasswordOut(must_change_password=outcome.must_change_password)


@router.post("/refresh", response_model=MustChangePasswordOut, summary="刷新令牌")
async def refresh(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> MustChangePasswordOut:
    outcome = await service.refresh(
        session,
        redis,
        request.cookies.get(REFRESH_COOKIE),
        client_ip(request),
        request.headers.get("user-agent"),
    )
    if outcome.error is not None:
        # RefreshInvalidError 会在错误处理器里清除两个 Cookie。
        raise outcome.error

    assert outcome.tokens is not None
    set_auth_cookies(
        response,
        outcome.tokens.access_token,
        outcome.tokens.refresh_token,
        outcome.tokens.access_minutes,
        outcome.tokens.refresh_days,
    )
    return MustChangePasswordOut(must_change_password=outcome.must_change_password)


@router.post("/logout", status_code=204, summary="登出")
async def logout(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await service.logout(session, request.cookies.get(ACCESS_COOKIE))
    clear_auth_cookies(response)


@router.post(
    "/change-password",
    response_model=MustChangePasswordOut,
    summary="修改密码",
    dependencies=[Depends(audited("auth.change_password"))],
)
async def change_password(
    body: ChangePasswordIn,
    request: Request,
    response: Response,
    ctx: Annotated[Ctx, Depends(current_ctx_allow_pwc)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> MustChangePasswordOut:
    result = await service.change_password(
        session,
        redis,
        ctx,
        body.old_password,
        body.new_password,
        request.headers.get("user-agent"),
    )
    set_auth_cookies(
        response,
        result.tokens.access_token,
        result.tokens.refresh_token,
        result.tokens.access_minutes,
        result.tokens.refresh_days,
    )
    await audit_success(
        request,
        AuditTarget("user", result.user.id, result.user.username, result.user.org_id),
    )
    return MustChangePasswordOut(must_change_password=False)


@router.get("/policy", response_model=AuthPolicyOut, summary="密码与令牌策略")
async def get_policy(
    ctx: Annotated[Ctx, Depends(current_ctx_allow_pwc)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthPolicyOut:
    cfg = await settings_service.get_settings(session)
    return AuthPolicyOut(
        password_min_length=cfg.password.min_length,
        password_min_classes=cfg.password.min_classes,
        access_token_minutes=cfg.session.access_minutes,
    )
