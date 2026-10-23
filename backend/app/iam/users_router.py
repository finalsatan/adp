"""用户管理接口：列表、新建、详情、编辑、启停、重置、解锁、任命与个人资料。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget, record
from app.core.db import get_session
from app.core.redis import get_redis
from app.core.schemas import AuditResult, Page, Pagination, Role, UserStatus
from app.iam import users_service
from app.iam.deps import CurrentCtx, require_perm
from app.iam.schemas import (
    ResetPasswordIn,
    RevokeSysAdminIn,
    UserCreate,
    UserOut,
    UserPatch,
    UserSearchOut,
)

router = APIRouter(prefix="/api/v1", tags=["用户"])

USER_MANAGE = [Depends(require_perm("user.manage"))]


def _target(out: UserOut, org_id: int | None = None) -> AuditTarget:
    """审计对象：类型 user，desc 用户名，对象组织默认取变更后的所属组织。"""
    if org_id is None and out.org is not None:
        org_id = out.org.id
    return AuditTarget("user", out.id, out.username, org_id)


@router.get(
    "/users:search",
    response_model=list[UserSearchOut],
    dependencies=[Depends(require_perm("console.use"))],
    summary="搜索用户",
)
async def search_users(
    q: Annotated[str, Query(min_length=1, max_length=50)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[UserSearchOut]:
    return await users_service.search_users(session, q)


@router.get("/users", response_model=Page[UserOut], dependencies=USER_MANAGE, summary="用户列表")
async def list_users(
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    org_id: int | None = None,
    include_sub: bool = True,
    role: Role | None = None,
    status: UserStatus | None = None,
    keyword: str | None = None,
) -> Page[UserOut]:
    return await users_service.list_users(
        session, redis, ctx, pagination, org_id, include_sub, role, status, keyword
    )


@router.post(
    "/users",
    response_model=UserOut,
    status_code=201,
    dependencies=[Depends(audited("user.create")), *USER_MANAGE],
    summary="新建用户",
)
async def create_user(
    body: UserCreate,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> UserOut:
    user = await users_service.create_user(session, redis, ctx, body)
    await audit_success(request, _target(user))
    return user


@router.get("/users/{id:int}", response_model=UserOut, dependencies=USER_MANAGE, summary="用户详情")
async def get_user(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> UserOut:
    return await users_service.get_user(session, redis, ctx, id)


@router.patch(
    "/users/{id:int}",
    response_model=UserOut,
    dependencies=[Depends(audited("user.update")), *USER_MANAGE],
    summary="编辑用户",
)
async def patch_user(
    id: int,
    body: UserPatch,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> UserOut:
    user, events = await users_service.patch_user(session, redis, ctx, id, body)
    target = _target(user)
    for action, detail in events:
        await record(ctx, action, target, AuditResult.success, detail)
    return user


@router.post(
    "/users/{id:int}:disable",
    response_model=UserOut,
    dependencies=[Depends(audited("user.disable")), *USER_MANAGE],
    summary="停用用户",
)
async def disable_user(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> UserOut:
    user = await users_service.disable_user(session, redis, ctx, id)
    await audit_success(request, _target(user))
    return user


@router.post(
    "/users/{id:int}:enable",
    response_model=UserOut,
    dependencies=[Depends(audited("user.enable")), *USER_MANAGE],
    summary="启用用户",
)
async def enable_user(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> UserOut:
    user = await users_service.enable_user(session, redis, ctx, id)
    await audit_success(request, _target(user))
    return user


@router.post(
    "/users/{id:int}:reset-password",
    status_code=204,
    dependencies=[Depends(audited("user.reset_password")), *USER_MANAGE],
    summary="重置用户密码",
)
async def reset_user_password(
    id: int,
    body: ResetPasswordIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> None:
    user = await users_service.reset_password(session, redis, ctx, id, body.new_password)
    await audit_success(request, AuditTarget("user", user.id, user.username, user.org_id))


@router.post(
    "/users/{id:int}:unlock",
    status_code=204,
    dependencies=[Depends(audited("user.unlock")), *USER_MANAGE],
    summary="解锁用户",
)
async def unlock_user(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> None:
    user = await users_service.unlock_user(session, redis, ctx, id)
    await audit_success(request, AuditTarget("user", user.id, user.username, user.org_id))


@router.post(
    "/users/{id:int}:grant-sys-admin",
    response_model=UserOut,
    dependencies=[Depends(audited("user.grant_sys_admin")), *USER_MANAGE],
    summary="任命系统管理员",
)
async def grant_sys_admin(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> UserOut:
    user, original_org_id = await users_service.grant_sys_admin(session, redis, ctx, id)
    await audit_success(request, _target(user, org_id=original_org_id))
    return user


@router.post(
    "/users/{id:int}:revoke-sys-admin",
    response_model=UserOut,
    dependencies=[Depends(audited("user.revoke_sys_admin")), *USER_MANAGE],
    summary="撤销系统管理员",
)
async def revoke_sys_admin(
    id: int,
    body: RevokeSysAdminIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> UserOut:
    user = await users_service.revoke_sys_admin(
        session, redis, ctx, id, body.org_id, Role(body.role)
    )
    await audit_success(request, _target(user))
    return user
