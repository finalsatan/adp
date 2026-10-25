"""知识库的文件夹与元数据字段接口（P3-01 契约，实现见 P3-05）。

路由只做参数接收、权限依赖、调用 service；不写审计（需求 4.1.11 未把改文件夹、
元数据字段列入审计清单）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.iam.deps import CurrentCtx, require_perm
from app.kb import folders, metadata
from app.kb.schemas import (
    FolderCreate,
    FolderOut,
    FolderUpdate,
    MetadataFieldCreate,
    MetadataFieldOut,
    MetadataFieldUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["知识库"])

CONSOLE_USE = [Depends(require_perm("console.use"))]


@router.get(
    "/kbs/{id:int}/folders",
    response_model=list[FolderOut],
    dependencies=CONSOLE_USE,
    summary="文件夹列表",
)
async def list_folders(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[FolderOut]:
    return await folders.list_folders(session, ctx, id)


@router.post(
    "/kbs/{id:int}/folders",
    response_model=FolderOut,
    status_code=201,
    summary="新建文件夹",
)
async def create_folder(
    id: int,
    body: FolderCreate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FolderOut:
    return await folders.create_folder(session, ctx, id, body.parent_id, body.name)


@router.patch(
    "/folders/{id:int}",
    response_model=FolderOut,
    summary="重命名文件夹",
)
async def rename_folder(
    id: int,
    body: FolderUpdate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FolderOut:
    return await folders.rename_folder(session, ctx, id, body.name)


@router.delete(
    "/folders/{id:int}",
    status_code=204,
    summary="删除文件夹",
)
async def delete_folder(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await folders.delete_folder(session, ctx, id)


@router.get(
    "/kbs/{id:int}/metadata-fields",
    response_model=list[MetadataFieldOut],
    dependencies=CONSOLE_USE,
    summary="元数据字段列表",
)
async def list_metadata_fields(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MetadataFieldOut]:
    return await metadata.list_fields(session, ctx, id)


@router.post(
    "/kbs/{id:int}/metadata-fields",
    response_model=MetadataFieldOut,
    status_code=201,
    summary="新建元数据字段",
)
async def create_metadata_field(
    id: int,
    body: MetadataFieldCreate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MetadataFieldOut:
    return await metadata.create_field(session, ctx, id, body.name, body.type, body.options)


@router.patch(
    "/metadata-fields/{id:int}",
    response_model=MetadataFieldOut,
    summary="编辑元数据字段",
)
async def update_metadata_field(
    id: int,
    body: MetadataFieldUpdate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MetadataFieldOut:
    return await metadata.update_field(session, ctx, id, body.name, body.options)


@router.delete(
    "/metadata-fields/{id:int}",
    status_code=204,
    summary="删除元数据字段",
)
async def delete_metadata_field(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await metadata.delete_field(session, ctx, id)
