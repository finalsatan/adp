"""资源总览与协作者接口。权限判断在 `access`，业务逻辑在 `service`。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget
from app.core.db import get_session
from app.core.schemas import Page, Pagination, ResourceStatus, Visibility
from app.iam.deps import CurrentCtx, require_perm
from app.resources import registry, service
from app.resources.schemas import MemberIn, MemberOut, ResourceOut, ResourceTypeOut, VisibilityIn
from app.resources.tables import Resource

router = APIRouter(prefix="/api/v1", tags=["资源"])


def _target(resource: Resource) -> AuditTarget:
    """审计对象：类型为资源类型代码，desc 为资源名，对象组织为资源所属组织。"""
    return AuditTarget(resource.type, resource.id, resource.name, resource.org_id)


@router.get(
    "/resources",
    response_model=Page[ResourceOut],
    dependencies=[Depends(require_perm("overview.view"))],
    summary="资源列表",
)
async def list_resources(
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    type: str | None = None,
    org_id: int | None = None,
    include_sub: bool = True,
    owner_id: int | None = None,
    visibility: Visibility | None = None,
    status: ResourceStatus | None = None,
    keyword: str | None = None,
) -> Page[ResourceOut]:
    return await service.list_resources(
        session,
        ctx,
        pagination,
        type,
        org_id,
        include_sub,
        owner_id,
        visibility,
        status,
        keyword,
    )


@router.get(
    "/resource-types",
    response_model=list[ResourceTypeOut],
    dependencies=[Depends(require_perm("console.use"))],
    summary="资源类型列表",
)
async def list_resource_types() -> list[ResourceTypeOut]:
    return [
        ResourceTypeOut(
            code=resource_type.code,
            label=resource_type.label,
            fixed_visibility=resource_type.fixed_visibility,
        )
        for resource_type in registry.list_resource_types()
    ]


@router.patch(
    "/resources/{id:int}/visibility",
    response_model=ResourceOut,
    dependencies=[Depends(audited("resource.change_visibility"))],
    summary="修改资源可见范围",
)
async def patch_resource_visibility(
    id: int,
    body: VisibilityIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ResourceOut:
    resource, detail = await service.change_visibility(session, ctx, id, body.visibility)
    await audit_success(request, _target(resource), detail)
    return await service.resource_out(session, resource)


@router.get(
    "/resources/{id:int}/members",
    response_model=list[MemberOut],
    summary="资源协作者列表",
)
async def list_resource_members(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MemberOut]:
    return await service.list_members(session, ctx, id)


@router.post(
    "/resources/{id:int}/members",
    response_model=MemberOut,
    status_code=201,
    dependencies=[Depends(audited("resource.add_member"))],
    summary="添加协作者",
)
async def add_resource_member(
    id: int,
    body: MemberIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberOut:
    member, resource = await service.add_member(session, ctx, id, body.user_id)
    await audit_success(request, _target(resource), {"username": member.user.username})
    return member


@router.delete(
    "/resources/{id:int}/members/{user_id:int}",
    status_code=204,
    dependencies=[Depends(audited("resource.remove_member"))],
    summary="移除协作者",
)
async def remove_resource_member(
    id: int,
    user_id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    resource = await service.remove_member(session, ctx, id, user_id)
    await audit_success(request, _target(resource))


@router.post(
    "/resources/{id:int}:disable",
    response_model=ResourceOut,
    dependencies=[Depends(audited("resource.disable"))],
    summary="停用资源",
)
async def disable_resource(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ResourceOut:
    resource = await service.set_status(session, ctx, id, enabled=False)
    await audit_success(request, _target(resource))
    return await service.resource_out(session, resource)


@router.post(
    "/resources/{id:int}:enable",
    response_model=ResourceOut,
    dependencies=[Depends(audited("resource.enable"))],
    summary="启用资源",
)
async def enable_resource(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ResourceOut:
    resource = await service.set_status(session, ctx, id, enabled=True)
    await audit_success(request, _target(resource))
    return await service.resource_out(session, resource)


@router.delete(
    "/resources/{id:int}",
    status_code=204,
    dependencies=[Depends(audited("resource.delete"))],
    summary="删除资源",
)
async def delete_resource(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    resource = await service.delete_resource(session, ctx, id)
    await audit_success(request, _target(resource))
