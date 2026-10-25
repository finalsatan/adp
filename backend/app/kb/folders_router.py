"""知识库的文件夹与元数据字段接口（P3-01 契约，实现见 P3-05）。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.iam.deps import CurrentCtx, require_perm
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
    id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[FolderOut]:
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


@router.get(
    "/kbs/{id:int}/metadata-fields",
    response_model=list[MetadataFieldOut],
    dependencies=CONSOLE_USE,
    summary="元数据字段列表",
)
async def list_metadata_fields(
    id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[MetadataFieldOut]:
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError
