"""用户管理接口。实现留空，P1-11 填充。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.schemas import Page, Pagination, Role, UserStatus
from app.iam.deps import require_perm
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


@router.get(
    "/users:search",
    response_model=list[UserSearchOut],
    dependencies=[Depends(require_perm("console.use"))],
    summary="搜索用户",
)
async def search_users(
    q: Annotated[str, Query(min_length=1, max_length=50)],
) -> list[UserSearchOut]:
    raise NotImplementedError


@router.get("/users", response_model=Page[UserOut], dependencies=USER_MANAGE, summary="用户列表")
async def list_users(
    pagination: Annotated[Pagination, Depends()],
    org_id: int | None = None,
    include_sub: bool = True,
    role: Role | None = None,
    status: UserStatus | None = None,
    keyword: str | None = None,
) -> Page[UserOut]:
    raise NotImplementedError


@router.post(
    "/users",
    response_model=UserOut,
    status_code=201,
    dependencies=USER_MANAGE,
    summary="新建用户",
)
async def create_user(body: UserCreate) -> UserOut:
    raise NotImplementedError


@router.get("/users/{id:int}", response_model=UserOut, dependencies=USER_MANAGE, summary="用户详情")
async def get_user(id: int) -> UserOut:
    raise NotImplementedError


@router.patch(
    "/users/{id:int}", response_model=UserOut, dependencies=USER_MANAGE, summary="编辑用户"
)
async def patch_user(id: int, body: UserPatch) -> UserOut:
    raise NotImplementedError


@router.post(
    "/users/{id:int}:disable",
    response_model=UserOut,
    dependencies=USER_MANAGE,
    summary="停用用户",
)
async def disable_user(id: int) -> UserOut:
    raise NotImplementedError


@router.post(
    "/users/{id:int}:enable",
    response_model=UserOut,
    dependencies=USER_MANAGE,
    summary="启用用户",
)
async def enable_user(id: int) -> UserOut:
    raise NotImplementedError


@router.post(
    "/users/{id:int}:reset-password",
    status_code=204,
    dependencies=USER_MANAGE,
    summary="重置用户密码",
)
async def reset_user_password(id: int, body: ResetPasswordIn) -> None:
    raise NotImplementedError


@router.post(
    "/users/{id:int}:unlock",
    status_code=204,
    dependencies=USER_MANAGE,
    summary="解锁用户",
)
async def unlock_user(id: int) -> None:
    raise NotImplementedError


@router.post(
    "/users/{id:int}:grant-sys-admin",
    response_model=UserOut,
    dependencies=USER_MANAGE,
    summary="任命系统管理员",
)
async def grant_sys_admin(id: int) -> UserOut:
    raise NotImplementedError


@router.post(
    "/users/{id:int}:revoke-sys-admin",
    response_model=UserOut,
    dependencies=USER_MANAGE,
    summary="撤销系统管理员",
)
async def revoke_sys_admin(id: int, body: RevokeSysAdminIn) -> UserOut:
    raise NotImplementedError
