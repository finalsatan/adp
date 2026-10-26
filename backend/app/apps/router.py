"""应用、草稿与版本、使用范围、我的应用接口（P4-01 契约）。

路由只做参数接收、权限依赖、调用 service、写审计；本卡是占位（返回 501），
实现见 P4-03（应用管理）、P4-04（草稿与版本）、P4-05（使用范围与我的应用）、
P4-08（prompt-preview）。

资源级“查看 / 编辑 / 管理”判断留给实现卡，占位只要求登录（同 P3-01）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.core.db import get_session
from app.core.schemas import Page, Pagination, Visibility
from app.iam.context import current_ctx
from app.iam.deps import CurrentCtx, require_perm
from app.resources.schemas import MemberIn, MemberOut, VisibilityIn

router = APIRouter(prefix="/api/v1", tags=["应用"])

CONSOLE_USE = [Depends(require_perm("console.use"))]
RESOURCE_CREATE = [Depends(require_perm("resource.create"))]
# 资源“编辑 / 管理”没有对应权限码，只要求登录，资源级判断在实现卡。
AUTH = [Depends(current_ctx)]


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
    raise NotImplementedError


@router.post(
    "/apps",
    response_model=AppOut,
    status_code=201,
    dependencies=RESOURCE_CREATE,
    summary="新建应用",
)
async def create_app(
    body: AppCreate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    raise NotImplementedError


@router.get("/apps/{id:int}", response_model=AppOut, dependencies=CONSOLE_USE, summary="应用详情")
async def get_app(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    raise NotImplementedError


@router.patch("/apps/{id:int}", response_model=AppOut, dependencies=AUTH, summary="编辑应用")
async def update_app(
    id: int,
    body: AppUpdate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    raise NotImplementedError


@router.delete("/apps/{id:int}", status_code=204, dependencies=AUTH, summary="删除应用")
async def delete_app(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    raise NotImplementedError


@router.post(
    "/apps/{id:int}:disable",
    response_model=AppOut,
    dependencies=AUTH,
    summary="停用应用",
)
async def disable_app(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    raise NotImplementedError


@router.post(
    "/apps/{id:int}:enable",
    response_model=AppOut,
    dependencies=AUTH,
    summary="启用应用",
)
async def enable_app(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    raise NotImplementedError


@router.patch(
    "/apps/{id:int}/visibility",
    response_model=AppOut,
    dependencies=AUTH,
    summary="调整可见范围",
)
async def change_app_visibility(
    id: int,
    body: VisibilityIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppOut:
    raise NotImplementedError


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
    raise NotImplementedError


@router.post(
    "/apps/{id:int}/members",
    response_model=MemberOut,
    status_code=201,
    dependencies=AUTH,
    summary="添加协作者",
)
async def add_app_member(
    id: int,
    body: MemberIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberOut:
    raise NotImplementedError


@router.delete(
    "/apps/{id:int}/members/{user_id:int}",
    status_code=204,
    dependencies=AUTH,
    summary="移除协作者",
)
async def remove_app_member(
    id: int,
    user_id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    raise NotImplementedError


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
    raise NotImplementedError


@router.put(
    "/apps/{id:int}/usage-scope",
    response_model=UsageScopeOut,
    dependencies=AUTH,
    summary="修改使用范围",
)
async def put_app_usage_scope(
    id: int,
    body: UsageScopeIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsageScopeOut:
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


@router.post(
    "/apps/{id:int}:publish",
    response_model=AppVersionOut,
    dependencies=AUTH,
    summary="发布应用",
)
async def publish_app(
    id: int,
    body: PublishIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppVersionOut:
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


@router.post(
    "/apps/{id:int}/versions/{v:int}:restore",
    response_model=DraftOut,
    dependencies=AUTH,
    summary="恢复历史版本为草稿",
)
async def restore_app_version(
    id: int,
    v: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DraftOut:
    raise NotImplementedError
