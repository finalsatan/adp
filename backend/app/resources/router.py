"""资源总览与协作者接口。实现留空，P1-12 填充。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.schemas import Page, Pagination, ResourceStatus, Visibility
from app.iam.deps import CurrentCtx, require_perm
from app.resources.schemas import MemberIn, MemberOut, ResourceOut, ResourceTypeOut, VisibilityIn

router = APIRouter(prefix="/api/v1", tags=["资源"])


@router.get(
    "/resources",
    response_model=Page[ResourceOut],
    dependencies=[Depends(require_perm("overview.view"))],
    summary="资源列表",
)
async def list_resources(
    pagination: Annotated[Pagination, Depends()],
    type: str | None = None,
    org_id: int | None = None,
    include_sub: bool = True,
    owner_id: int | None = None,
    visibility: Visibility | None = None,
    status: ResourceStatus | None = None,
    keyword: str | None = None,
) -> Page[ResourceOut]:
    raise NotImplementedError


@router.get(
    "/resource-types",
    response_model=list[ResourceTypeOut],
    dependencies=[Depends(require_perm("console.use"))],
    summary="资源类型列表",
)
async def list_resource_types() -> list[ResourceTypeOut]:
    raise NotImplementedError


@router.patch(
    "/resources/{id:int}/visibility",
    response_model=ResourceOut,
    summary="修改资源可见范围",
)
async def patch_resource_visibility(
    id: int,
    body: VisibilityIn,
    ctx: CurrentCtx,
) -> ResourceOut:
    raise NotImplementedError


@router.get(
    "/resources/{id:int}/members",
    response_model=list[MemberOut],
    summary="资源协作者列表",
)
async def list_resource_members(id: int, ctx: CurrentCtx) -> list[MemberOut]:
    raise NotImplementedError


@router.post(
    "/resources/{id:int}/members",
    response_model=MemberOut,
    status_code=201,
    summary="添加协作者",
)
async def add_resource_member(id: int, body: MemberIn, ctx: CurrentCtx) -> MemberOut:
    raise NotImplementedError


@router.delete(
    "/resources/{id:int}/members/{user_id:int}",
    status_code=204,
    summary="移除协作者",
)
async def remove_resource_member(id: int, user_id: int, ctx: CurrentCtx) -> None:
    raise NotImplementedError


@router.post(
    "/resources/{id:int}:disable",
    response_model=ResourceOut,
    summary="停用资源",
)
async def disable_resource(id: int, ctx: CurrentCtx) -> ResourceOut:
    raise NotImplementedError


@router.post(
    "/resources/{id:int}:enable",
    response_model=ResourceOut,
    summary="启用资源",
)
async def enable_resource(id: int, ctx: CurrentCtx) -> ResourceOut:
    raise NotImplementedError


@router.delete("/resources/{id:int}", status_code=204, summary="删除资源")
async def delete_resource(id: int, ctx: CurrentCtx) -> None:
    raise NotImplementedError
