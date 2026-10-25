"""知识库文档接口（P3-01 契约，实现见 P3-07、P3-12、P3-14）。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.schemas import Page, Pagination
from app.iam.deps import CurrentCtx, require_perm
from app.kb.schemas import (
    DocumentBatchIn,
    DocumentBatchOut,
    DocumentOut,
    DocumentPrecheckIn,
    DocumentPrecheckOut,
    DocumentStatus,
    DocumentUpdate,
    DocumentUploadOut,
    FileType,
    ParseResultOut,
    ReparseIn,
)

router = APIRouter(prefix="/api/v1", tags=["文档"])

CONSOLE_USE = [Depends(require_perm("console.use"))]


@router.post(
    "/kbs/{id:int}/documents:precheck",
    response_model=DocumentPrecheckOut,
    summary="上传预检",
)
async def precheck_documents(
    id: int,
    body: DocumentPrecheckIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentPrecheckOut:
    raise NotImplementedError


@router.post(
    "/kbs/{id:int}/documents",
    response_model=DocumentUploadOut,
    status_code=201,
    summary="上传文档",
)
async def upload_documents(
    id: int,
    files: Annotated[list[UploadFile], File()],
    relative_paths: Annotated[list[str], Form()],
    schemes: Annotated[str, Form()],
    resolutions: Annotated[str, Form()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    folder_id: Annotated[int | None, Form()] = None,
) -> DocumentUploadOut:
    raise NotImplementedError


@router.get(
    "/kbs/{id:int}/documents",
    response_model=Page[DocumentOut],
    dependencies=CONSOLE_USE,
    summary="文档列表",
)
async def list_documents(
    id: int,
    pagination: Annotated[Pagination, Depends()],
    session: Annotated[AsyncSession, Depends(get_session)],
    folder_id: int | None = None,
    include_sub: bool = True,
    status: DocumentStatus | None = None,
    file_type: FileType | None = None,
    scheme_id: int | None = None,
    keyword: str | None = None,
    metadata: str | None = None,
) -> Page[DocumentOut]:
    raise NotImplementedError


@router.post(
    "/documents:batch",
    response_model=DocumentBatchOut,
    summary="批量操作文档",
)
async def batch_documents(
    body: DocumentBatchIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentBatchOut:
    raise NotImplementedError


@router.get(
    "/documents/{id:int}",
    response_model=DocumentOut,
    dependencies=CONSOLE_USE,
    summary="文档详情",
)
async def get_document(
    id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> DocumentOut:
    raise NotImplementedError


@router.patch(
    "/documents/{id:int}",
    response_model=DocumentOut,
    summary="编辑文档",
)
async def update_document(
    id: int,
    body: DocumentUpdate,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentOut:
    raise NotImplementedError


@router.delete(
    "/documents/{id:int}",
    status_code=204,
    summary="删除文档",
)
async def delete_document(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    raise NotImplementedError


@router.post(
    "/documents/{id:int}:retry",
    response_model=DocumentOut,
    summary="重试失败文档",
)
async def retry_document(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentOut:
    raise NotImplementedError


@router.post(
    "/documents/{id:int}:reparse",
    response_model=DocumentOut,
    summary="重新解析文档",
)
async def reparse_document(
    id: int,
    body: ReparseIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentOut:
    raise NotImplementedError


@router.get(
    "/documents/{id:int}/file",
    response_class=StreamingResponse,
    dependencies=CONSOLE_USE,
    summary="读取文档原文件或预览 PDF",
)
async def get_document_file(
    id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    variant: Literal["original", "preview"] = "original",
    download: bool = False,
) -> StreamingResponse:
    raise NotImplementedError


@router.get(
    "/documents/{id:int}/images/{name}",
    response_class=Response,
    dependencies=CONSOLE_USE,
    summary="读取文档图片",
)
async def get_document_image(
    id: int,
    name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    raise NotImplementedError


@router.get(
    "/documents/{id:int}/parse",
    response_model=ParseResultOut,
    dependencies=CONSOLE_USE,
    summary="解析结果与元素列表",
)
async def get_document_parse(
    id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    kind: Literal["original", "edited"] = "original",
) -> ParseResultOut:
    raise NotImplementedError
