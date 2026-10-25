"""知识库与检索接口（P3-01 契约，实现见 P3-04、P3-13）。

只写路由签名、返回结构和权限依赖，函数体留空返回 501。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.schemas import Page, Pagination, ResourceStatus, Visibility
from app.iam.deps import CurrentCtx, require_perm
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

router = APIRouter(prefix="/api/v1")

CONSOLE_USE = [Depends(require_perm("console.use"))]
RESOURCE_CREATE = [Depends(require_perm("resource.create"))]


@router.get(
    "/kbs",
    response_model=Page[KbOut],
    dependencies=CONSOLE_USE,
    tags=["知识库"],
    summary="知识库列表",
)
async def list_kbs(
    pagination: Annotated[Pagination, Depends()],
    session: Annotated[AsyncSession, Depends(get_session)],
    kind: KbKind | None = None,
    visibility: Visibility | None = None,
    org_id: int | None = None,
    include_sub: bool = True,
    status: ResourceStatus | None = None,
    keyword: str | None = None,
) -> Page[KbOut]:
    raise NotImplementedError


@router.post(
    "/kbs",
    response_model=KbOut,
    status_code=201,
    dependencies=RESOURCE_CREATE,
    tags=["知识库"],
    summary="新建知识库",
)
async def create_kb(
    body: KbCreate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    raise NotImplementedError


@router.get(
    "/kbs/{id:int}",
    response_model=KbOut,
    dependencies=CONSOLE_USE,
    tags=["知识库"],
    summary="知识库详情",
)
async def get_kb(id: int, session: Annotated[AsyncSession, Depends(get_session)]) -> KbOut:
    raise NotImplementedError


@router.patch(
    "/kbs/{id:int}",
    response_model=KbOut,
    tags=["知识库"],
    summary="编辑知识库",
)
async def update_kb(
    id: int,
    body: KbUpdate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    raise NotImplementedError


@router.delete(
    "/kbs/{id:int}",
    status_code=204,
    tags=["知识库"],
    summary="删除知识库",
)
async def delete_kb(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    raise NotImplementedError


@router.post(
    "/kbs/{id:int}:disable",
    response_model=KbOut,
    tags=["知识库"],
    summary="停用知识库",
)
async def disable_kb(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    raise NotImplementedError


@router.post(
    "/kbs/{id:int}:enable",
    response_model=KbOut,
    tags=["知识库"],
    summary="启用知识库",
)
async def enable_kb(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    raise NotImplementedError


@router.get(
    "/kbs/{id:int}/refs",
    response_model=list[KbRefOut],
    dependencies=CONSOLE_USE,
    tags=["知识库"],
    summary="知识库引用清单",
)
async def list_kb_refs(
    id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[KbRefOut]:
    raise NotImplementedError


@router.patch(
    "/kbs/{id:int}/visibility",
    response_model=KbOut,
    tags=["知识库"],
    summary="修改知识库可见范围",
)
async def change_kb_visibility(
    id: int,
    body: VisibilityIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbOut:
    raise NotImplementedError


@router.get(
    "/kbs/{id:int}/members",
    response_model=list[MemberOut],
    dependencies=CONSOLE_USE,
    tags=["知识库"],
    summary="知识库协作者列表",
)
async def list_kb_members(
    id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[MemberOut]:
    raise NotImplementedError


@router.post(
    "/kbs/{id:int}/members",
    response_model=MemberOut,
    status_code=201,
    tags=["知识库"],
    summary="添加知识库协作者",
)
async def add_kb_member(
    id: int,
    body: MemberIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberOut:
    raise NotImplementedError


@router.delete(
    "/kbs/{id:int}/members/{user_id:int}",
    status_code=204,
    tags=["知识库"],
    summary="移除知识库协作者",
)
async def remove_kb_member(
    id: int,
    user_id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    raise NotImplementedError


@router.get(
    "/kbs/{id:int}/retrieval-config",
    response_model=RetrievalConfig,
    dependencies=CONSOLE_USE,
    tags=["检索"],
    summary="查看检索配置",
)
async def get_retrieval_config(
    id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RetrievalConfig:
    raise NotImplementedError


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
    raise NotImplementedError


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
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HitTestOut:
    raise NotImplementedError
