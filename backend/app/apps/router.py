"""应用、草稿与版本、使用范围、我的应用接口（P4-01 契约）。

路由只做参数接收、权限依赖、调用 service、写审计。应用管理已实现（P4-03）；
草稿与版本已实现（P4-04）；使用范围、我的应用已实现（P4-05）；prompt-preview 由
P4-08 实现（固定部分预览）。

资源级“查看 / 编辑 / 管理”判断留给实现卡，占位只要求登录（同 P3-01）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps import draft, refs, service, usage, versions
from app.apps.schemas import (
    AppCreate,
    AppOut,
    AppStatus,
    AppType,
    AppUpdate,
    AppVersionOut,
    DraftIn,
    DraftOut,
    KbCandidateOut,
    MyAppOut,
    PromptPreviewOut,
    PublishIn,
    UsageScopeIn,
    UsageScopeOut,
)
from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget
from app.chat import prompts
from app.core.db import get_session
from app.core.schemas import Page, Pagination, Visibility
from app.iam.context import current_ctx
from app.iam.deps import CurrentCtx, require_perm
from app.resources import access
from app.resources.schemas import MemberIn, MemberOut, VisibilityIn
from app.resources.tables import Resource

router = APIRouter(prefix="/api/v1", tags=["应用"])

CONSOLE_USE = [Depends(require_perm("console.use"))]
RESOURCE_CREATE = [Depends(require_perm("resource.create"))]
# 资源“编辑 / 管理”没有对应权限码，只要求登录，资源级判断在 service。
AUTH = [Depends(current_ctx)]


def _target(resource: Resource) -> AuditTarget:
    """审计对象：类型为资源类型代码 `app`，desc 为资源名，对象组织为资源所属组织。"""
    return AuditTarget("app", resource.id, resource.name, resource.org_id)


def _app_target(out: AppOut) -> AuditTarget:
    return AuditTarget("app", out.id, out.name, out.org.id)


@router.get("/apps", response_model=Page[AppOut], dependencies=CONSOLE_USE, summary="应用列表")
async def list_apps(
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    app_type: AppType | None = None,
    status: AppStatus | None = None,
    visibility: Visibility | None = None,
    org_id: int | None = None,
    include_sub: bool = True,
    keyword: str | None = None,
) -> Page[AppOut]:
    return await service.list_apps(
        session, ctx, pagination, app_type, status, visibility, org_id, include_sub, keyword
    )


@router.post(
    "/apps",
    response_model=AppOut,
    status_code=201,
    dependencies=[Depends(audited("app.create")), Depends(require_perm("resource.create"))],
    summary="新建应用",
)
async def create_app(
    body: AppCreate,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    out = await service.create_app(session, ctx, body)
    await audit_success(request, _app_target(out))
    return out


@router.get("/apps/{id:int}", response_model=AppOut, dependencies=CONSOLE_USE, summary="应用详情")
async def get_app(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    return await service.get_app(session, ctx, id)


@router.patch(
    "/apps/{id:int}",
    response_model=AppOut,
    dependencies=[Depends(audited("app.update"))],
    summary="编辑应用",
)
async def update_app(
    id: int,
    body: AppUpdate,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    out = await service.update_app(session, ctx, id, body)
    await audit_success(request, _app_target(out))
    return out


@router.delete(
    "/apps/{id:int}",
    status_code=204,
    dependencies=[Depends(audited("app.delete"))],
    summary="删除应用",
)
async def delete_app(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    resource = await service.delete_app(session, ctx, id)
    await audit_success(request, _target(resource))


@router.post(
    "/apps/{id:int}:disable",
    response_model=AppOut,
    dependencies=[Depends(audited("app.disable"))],
    summary="停用应用",
)
async def disable_app(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    out = await service.set_app_status(session, ctx, id, enabled=False)
    await audit_success(request, _app_target(out))
    return out


@router.post(
    "/apps/{id:int}:enable",
    response_model=AppOut,
    dependencies=[Depends(audited("app.enable"))],
    summary="启用应用",
)
async def enable_app(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    out = await service.set_app_status(session, ctx, id, enabled=True)
    await audit_success(request, _app_target(out))
    return out


@router.patch(
    "/apps/{id:int}/visibility",
    response_model=AppOut,
    dependencies=[Depends(audited("app.change_visibility"))],
    summary="调整可见范围",
)
async def change_app_visibility(
    id: int,
    body: VisibilityIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    out, detail = await service.change_app_visibility(session, ctx, id, body.visibility)
    await audit_success(request, _app_target(out), detail)
    return out


@router.get(
    "/apps/{id:int}/members",
    response_model=list[MemberOut],
    dependencies=CONSOLE_USE,
    summary="协作者列表",
)
async def list_app_members(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MemberOut]:
    return await service.list_app_members(session, ctx, id)


@router.post(
    "/apps/{id:int}/members",
    response_model=MemberOut,
    status_code=201,
    dependencies=[Depends(audited("app.add_member"))],
    summary="添加协作者",
)
async def add_app_member(
    id: int,
    body: MemberIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberOut:
    member, resource = await service.add_app_member(session, ctx, id, body.user_id)
    await audit_success(request, _target(resource), {"username": member.user.username})
    return member


@router.delete(
    "/apps/{id:int}/members/{user_id:int}",
    status_code=204,
    dependencies=[Depends(audited("app.remove_member"))],
    summary="移除协作者",
)
async def remove_app_member(
    id: int,
    user_id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    resource = await service.remove_app_member(session, ctx, id, user_id)
    await audit_success(request, _target(resource))


@router.get(
    "/apps/{id:int}/usage-scope",
    response_model=UsageScopeOut,
    dependencies=CONSOLE_USE,
    summary="读取使用范围",
)
async def get_app_usage_scope(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsageScopeOut:
    return await usage.get_usage_scope(session, ctx, id)


@router.put(
    "/apps/{id:int}/usage-scope",
    response_model=UsageScopeOut,
    dependencies=[Depends(audited("app.change_usage_scope"))],
    summary="修改使用范围",
)
async def put_app_usage_scope(
    id: int,
    body: UsageScopeIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsageScopeOut:
    out, detail = await usage.set_usage_scope(session, ctx, id, body)
    resource = await session.get(Resource, id)
    if resource is not None:
        await audit_success(request, _target(resource), detail)
    return out


@router.get(
    "/my/apps",
    response_model=list[MyAppOut],
    dependencies=AUTH,
    summary="我的应用",
)
async def list_my_apps(
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MyAppOut]:
    return await usage.list_my_apps(session, ctx)


@router.get(
    "/apps/{id:int}/draft",
    response_model=DraftOut,
    dependencies=AUTH,
    summary="读取草稿",
)
async def get_app_draft(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DraftOut:
    return await draft.get_draft(session, ctx, id)


@router.put(
    "/apps/{id:int}/draft",
    response_model=DraftOut,
    dependencies=AUTH,
    summary="保存草稿",
)
async def put_app_draft(
    id: int,
    body: DraftIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DraftOut:
    return await draft.save_draft(session, ctx, id, body)


@router.get(
    "/apps/{id:int}/prompt-preview",
    response_model=PromptPreviewOut,
    dependencies=CONSOLE_USE,
    summary="系统拼接部分预览",
)
async def get_app_prompt_preview(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PromptPreviewOut:
    # 权限：应用“查看”（应用技术方案 14、P1-12）；资源级判断在 access.get_for。
    await access.get_for(session, ctx, id, access.VIEW, type="app")
    return PromptPreviewOut(
        fixed_part=prompts.FIXED_PART,
        default_system_prompt=prompts.DEFAULT_SYSTEM_PROMPT,
        prompt_version=prompts.PROMPT_VERSION,
    )


@router.get(
    "/apps/{id:int}/kb-candidates",
    response_model=list[KbCandidateOut],
    dependencies=AUTH,
    summary="可添加的知识库",
)
async def list_app_kb_candidates(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[KbCandidateOut]:
    return await refs.list_kb_candidates(session, ctx, id)


@router.post(
    "/apps/{id:int}:publish",
    response_model=AppVersionOut,
    dependencies=[Depends(audited("app.publish"))],
    summary="发布应用",
)
async def publish_app(
    id: int,
    body: PublishIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppVersionOut:
    out = await versions.publish(session, ctx, id, body)
    resource = await session.get(Resource, id)
    if resource is not None:
        await audit_success(request, _target(resource), {"version_no": out.version_no})
    return out


@router.get(
    "/apps/{id:int}/versions",
    response_model=Page[AppVersionOut],
    dependencies=CONSOLE_USE,
    summary="发布历史",
)
async def list_app_versions(
    id: int,
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Page[AppVersionOut]:
    return await versions.list_versions(session, ctx, id, pagination)


@router.get(
    "/apps/{id:int}/versions/{v:int}",
    response_model=AppVersionOut,
    dependencies=CONSOLE_USE,
    summary="版本详情",
)
async def get_app_version(
    id: int,
    v: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppVersionOut:
    return await versions.get_version(session, ctx, id, v)


@router.post(
    "/apps/{id:int}/versions/{v:int}:restore",
    response_model=DraftOut,
    dependencies=[Depends(audited("app.restore"))],
    summary="恢复历史版本为草稿",
)
async def restore_app_version(
    id: int,
    v: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DraftOut:
    out = await versions.restore_version(session, ctx, id, v)
    resource = await session.get(Resource, id)
    if resource is not None:
        await audit_success(request, _target(resource), {"version_no": v})
    return out
