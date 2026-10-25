"""知识库文档接口（P3-01 契约，实现见 P3-07、P3-12、P3-14）。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget, record
from app.core.db import get_session
from app.core.schemas import AuditResult, Pagination
from app.iam.deps import CurrentCtx, require_perm
from app.kb.documents import files as files_service
from app.kb.documents import parse as parse_service
from app.kb.documents import service as documents_service
from app.kb.documents import upload as upload_service
from app.kb.schemas import (
    DocumentBatchAction,
    DocumentBatchIn,
    DocumentBatchOut,
    DocumentDetailOut,
    DocumentOut,
    DocumentPage,
    DocumentPrecheckIn,
    DocumentPrecheckOut,
    DocumentStatus,
    DocumentUpdate,
    DocumentUploadOut,
    FileType,
    ParseResultOut,
    ReparseIn,
    ReparseOut,
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
    return await upload_service.precheck_documents(session, ctx, id, body)


@router.post(
    "/kbs/{id:int}/documents",
    response_model=DocumentUploadOut,
    status_code=201,
    summary="上传文档",
)
async def upload_documents(
    id: int,
    request: Request,
    files: Annotated[list[UploadFile], File()],
    relative_paths: Annotated[list[str], Form()],
    schemes: Annotated[str, Form()],
    resolutions: Annotated[str, Form()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    folder_id: Annotated[int | None, Form()] = None,
) -> DocumentUploadOut:
    return await upload_service.upload_documents(
        session,
        ctx,
        id,
        files,
        relative_paths,
        folder_id,
        schemes,
        resolutions,
        storage=request.app.state.storage,
    )


@router.get(
    "/kbs/{id:int}/documents",
    response_model=DocumentPage,
    dependencies=CONSOLE_USE,
    summary="文档列表",
)
async def list_documents(
    id: int,
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    folder_id: int | None = None,
    include_sub: bool = True,
    uncategorized: bool = False,
    status: DocumentStatus | None = None,
    file_type: FileType | None = None,
    scheme_id: int | None = None,
    keyword: str | None = None,
    metadata: str | None = None,
    edited: bool | None = None,
) -> DocumentPage:
    return await documents_service.list_documents(
        session,
        ctx,
        id,
        pagination,
        folder_id=folder_id,
        include_sub=include_sub,
        uncategorized=uncategorized,
        status=status,
        file_type=file_type,
        scheme_id=scheme_id,
        keyword=keyword,
        metadata=metadata,
        edited=edited,
    )


@router.post(
    "/documents:batch",
    response_model=DocumentBatchOut,
    summary="批量操作文档",
)
async def batch_documents(
    body: DocumentBatchIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentBatchOut:
    outcome = await documents_service.batch_documents(
        session, ctx, body, storage=request.app.state.storage
    )
    # 只有批量删除写审计（知识库技术方案 17）；其余批量动作不写。
    if body.action == DocumentBatchAction.delete:
        target = AuditTarget(
            "document",
            None,
            f"批量删除 {len(outcome.result.succeeded)} 个文档",
            outcome.org_id,
        )
        await record(
            ctx,
            "document.batch_delete",
            target,
            AuditResult.success,
            {"doc_ids": outcome.result.succeeded, "kb_id": outcome.kb_id},
        )
    return outcome.result


@router.get(
    "/documents/{id:int}",
    response_model=DocumentDetailOut,
    dependencies=CONSOLE_USE,
    summary="文档详情",
)
async def get_document(
    id: int,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocumentDetailOut:
    return await documents_service.get_document_detail(session, ctx, id)


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
    return await documents_service.update_document(session, ctx, id, body)


@router.delete(
    "/documents/{id:int}",
    status_code=204,
    dependencies=[Depends(audited("document.delete"))],
    summary="删除文档",
)
async def delete_document(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    info = await documents_service.delete_document(
        session, ctx, id, storage=request.app.state.storage
    )
    await audit_success(
        request,
        AuditTarget("document", info.doc_id, info.name, info.org_id),
        {"kb_id": info.kb_id},
    )


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
    return await documents_service.retry_document(session, ctx, id)


@router.post(
    "/documents/{id:int}:reparse",
    response_model=ReparseOut,
    summary="重新解析文档",
)
async def reparse_document(
    id: int,
    body: ReparseIn,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReparseOut:
    return await documents_service.reparse_document(session, ctx, id, body)


@router.get(
    "/documents/{id:int}/file",
    response_class=StreamingResponse,
    dependencies=CONSOLE_USE,
    summary="读取文档原文件或预览 PDF",
)
async def get_document_file(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    variant: Literal["original", "preview"] = "original",
    download: bool = False,
) -> StreamingResponse:
    return await files_service.stream_document_file(
        session,
        ctx,
        request.app.state.storage,
        id,
        variant,
        download,
        request.headers.get("range"),
    )


@router.get(
    "/documents/{id:int}/images/{name}",
    response_class=Response,
    dependencies=CONSOLE_USE,
    summary="读取文档图片",
)
async def get_document_image(
    id: int,
    name: str,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    return await files_service.read_document_image(
        session, ctx, request.app.state.storage, id, name
    )


@router.get(
    "/documents/{id:int}/parse",
    response_model=ParseResultOut,
    dependencies=CONSOLE_USE,
    summary="解析结果与元素列表",
)
async def get_document_parse(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    kind: Literal["original", "edited"] = "original",
) -> ParseResultOut:
    return await parse_service.get_parse_result(session, ctx, request.app.state.storage, id, kind)
