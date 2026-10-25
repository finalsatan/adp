"""知识库与检索接口（P3-01 契约，实现见 P3-04、P3-13）。

知识库管理已实现（P3-04）；检索与命中测试仍是占位（P3-13）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget
from app.core.db import get_session
from app.core.redis import get_redis
from app.core.schemas import Page, Pagination, ResourceStatus, Visibility
from app.iam.deps import CurrentCtx, require_perm
from app.kb import retrieval, service
from app.kb.schemas import (
    HitTestIn,
    HitTestOut,
    KbCreate,
    KbKind,
    KbOut,
    KbRefOut,
    KbUpdate,
    RetrievalConfig,
)
from app.resources.schemas import MemberIn, MemberOut, VisibilityIn
from app.resources.tables import Resource

router = APIRouter(prefix="/api/v1")

CONSOLE_USE = [Depends(require_perm("console.use"))]
RESOURCE_CREATE = [Depends(require_perm("resource.create"))]


def _target(resource: Resource) -> AuditTarget:
    """审计对象：类型为资源类型代码 `kb`，desc 为资源名，对象组织为资源所属组织。"""
    return AuditTarget("kb", resource.id, resource.name, resource.org_id)


def _kb_target(out: KbOut) -> AuditTarget:
    return AuditTarget("kb", out.id, out.name, out.org.id)


@router.get(
    "/kbs",
    response_model=Page[KbOut],
    dependencies=CONSOLE_USE,
    tags=["知识库"],
    summary="知识库列表",
)
async def list_kbs(
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    kind: KbKind | None = None,
    visibility: Visibility | None = None,
    org_id: int | None = None,
    include_sub: bool = True,
    status: ResourceStatus | None = None,
    keyword: str | None = None,
) -> Page[KbOut]:
    return await service.list_kbs(
        session, ctx, pagination, kind, visibility, org_id, include_sub, status, keyword
    )


@router.post(
    "/kbs",
    response_model=KbOut,
    status_code=201,
    dependencies=[Depends(audited("kb.create")), Depends(require_perm("resource.create"))],
    tags=["知识库"],
    summary="新建知识库",
)
async def create_kb(
    body: KbCreate,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    out = await service.create_kb(session, ctx, body)
    await audit_success(request, _kb_target(out))
    return out


@router.get(
    "/kbs/{id:int}",
    response_model=KbOut,
    dependencies=CONSOLE_USE,
    tags=["知识库"],
    summary="知识库详情",
)
async def get_kb(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    return await service.get_kb(session, ctx, id)


@router.patch(
    "/kbs/{id:int}",
    response_model=KbOut,
    dependencies=[Depends(audited("kb.update"))],
    tags=["知识库"],
    summary="编辑知识库",
)
async def update_kb(
    id: int,
    body: KbUpdate,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    out = await service.update_kb(session, ctx, id, body)
    await audit_success(request, _kb_target(out))
    return out


@router.delete(
    "/kbs/{id:int}",
    status_code=204,
    dependencies=[Depends(audited("kb.delete"))],
    tags=["知识库"],
    summary="删除知识库",
)
async def delete_kb(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    resource = await service.delete_kb(session, ctx, id)
    await audit_success(request, _target(resource))


@router.post(
    "/kbs/{id:int}:disable",
    response_model=KbOut,
    dependencies=[Depends(audited("kb.disable"))],
    tags=["知识库"],
    summary="停用知识库",
)
async def disable_kb(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    out = await service.set_kb_status(session, ctx, id, enabled=False)
    await audit_success(request, _kb_target(out))
    return out


@router.post(
    "/kbs/{id:int}:enable",
    response_model=KbOut,
    dependencies=[Depends(audited("kb.enable"))],
    tags=["知识库"],
    summary="启用知识库",
)
async def enable_kb(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    out = await service.set_kb_status(session, ctx, id, enabled=True)
    await audit_success(request, _kb_target(out))
    return out


@router.get(
    "/kbs/{id:int}/refs",
    response_model=list[KbRefOut],
    dependencies=CONSOLE_USE,
    tags=["知识库"],
    summary="知识库引用清单",
)
async def list_kb_refs(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[KbRefOut]:
    return await service.list_refs(session, ctx, id)


@router.patch(
    "/kbs/{id:int}/visibility",
    response_model=KbOut,
    dependencies=[Depends(audited("kb.change_visibility"))],
    tags=["知识库"],
    summary="修改知识库可见范围",
)
async def change_kb_visibility(
    id: int,
    body: VisibilityIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    out, detail = await service.change_kb_visibility(session, ctx, id, body.visibility)
    await audit_success(request, _kb_target(out), detail)
    return out


@router.get(
    "/kbs/{id:int}/members",
    response_model=list[MemberOut],
    dependencies=CONSOLE_USE,
    tags=["知识库"],
    summary="知识库协作者列表",
)
async def list_kb_members(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MemberOut]:
    return await service.list_kb_members(session, ctx, id)


@router.post(
    "/kbs/{id:int}/members",
    response_model=MemberOut,
    status_code=201,
    dependencies=[Depends(audited("kb.add_member"))],
    tags=["知识库"],
    summary="添加知识库协作者",
)
async def add_kb_member(
    id: int,
    body: MemberIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberOut:
    member, resource = await service.add_kb_member(session, ctx, id, body.user_id)
    await audit_success(request, _target(resource), {"username": member.user.username})
    return member


@router.delete(
    "/kbs/{id:int}/members/{user_id:int}",
    status_code=204,
    dependencies=[Depends(audited("kb.remove_member"))],
    tags=["知识库"],
    summary="移除知识库协作者",
)
async def remove_kb_member(
    id: int,
    user_id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    resource = await service.remove_kb_member(session, ctx, id, user_id)
    await audit_success(request, _target(resource))


@router.get(
    "/kbs/{id:int}/retrieval-config",
    response_model=RetrievalConfig,
    dependencies=CONSOLE_USE,
    tags=["检索"],
    summary="查看检索配置",
)
async def get_retrieval_config(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RetrievalConfig:
    return await retrieval.get_config(session, ctx, id)


@router.put(
    "/kbs/{id:int}/retrieval-config",
    response_model=RetrievalConfig,
    tags=["检索"],
    summary="保存检索配置",
)
async def put_retrieval_config(
    id: int,
    body: RetrievalConfig,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RetrievalConfig:
    return await retrieval.save_config(session, ctx, id, body)


@router.post(
    "/kbs/{id:int}/hit-test",
    response_model=HitTestOut,
    dependencies=CONSOLE_USE,
    tags=["检索"],
    summary="命中测试",
)
async def hit_test(
    id: int,
    body: HitTestIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> HitTestOut:
    # 权限：能看该知识库即可用命中测试（技术方案 7.3）；记录不保存。
    await service.get_kb(session, ctx, id)
    return await retrieval.run_hit_test(request.app.state.sessionmaker, redis, ctx, id, body)
