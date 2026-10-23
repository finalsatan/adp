"""组织管理接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget
from app.core.db import get_session
from app.core.redis import get_redis
from app.core.schemas import Page, Pagination
from app.iam import orgs_service
from app.iam.deps import CurrentCtx, require_perm
from app.iam.schemas import OrgCreate, OrgMemberOut, OrgOut, OrgPatch

router = APIRouter(prefix="/api/v1", tags=["组织"])

# 写操作的 dependencies 里 audited 必须在第一个，权限不足时也能记 failure。
ORG_MANAGE = [Depends(require_perm("org.manage"))]


@router.get(
    "/orgs",
    response_model=list[OrgOut],
    dependencies=ORG_MANAGE,
    summary="组织列表",
)
async def list_orgs(
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[OrgOut]:
    return await orgs_service.list_orgs(session, ctx)


@router.post(
    "/orgs",
    response_model=OrgOut,
    status_code=201,
    dependencies=[Depends(audited("org.create")), *ORG_MANAGE],
    summary="新建组织",
)
async def create_org(
    body: OrgCreate,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgOut:
    org = await orgs_service.create_org(session, ctx, body)
    await audit_success(request, AuditTarget("org", org.id, org.name, org.id))
    return org


@router.patch(
    "/orgs/{id:int}",
    response_model=OrgOut,
    dependencies=[Depends(audited("org.update")), *ORG_MANAGE],
    summary="编辑组织",
)
async def patch_org(
    id: int,
    body: OrgPatch,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> OrgOut:
    org, changes = await orgs_service.patch_org(session, redis, ctx, id, body)
    await audit_success(request, AuditTarget("org", org.id, org.name, org.id), changes or None)
    return org


@router.delete(
    "/orgs/{id:int}",
    status_code=204,
    dependencies=[Depends(audited("org.delete")), *ORG_MANAGE],
    summary="删除组织",
)
async def delete_org(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    org = await orgs_service.delete_org(session, ctx, id)
    await audit_success(request, AuditTarget("org", org.id, org.name, org.id))


@router.get(
    "/orgs/{id:int}/members",
    response_model=Page[OrgMemberOut],
    dependencies=ORG_MANAGE,
    summary="组织成员",
)
async def list_org_members(
    id: int,
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Page[OrgMemberOut]:
    return await orgs_service.list_members(session, ctx, id, pagination)
