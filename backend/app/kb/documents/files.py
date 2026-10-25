"""文档原文件、预览 PDF 与图片读取（P3-14，知识库技术方案 11.1、13）。

对象存储的读写复用 P3-02 的 `Storage`，本模块只在其上做 HTTP Range 解析、
响应头（Content-Type / Content-Range / Content-Disposition / 安全头）和流式返回。

安全：只有 PDF、图片、纯文本白名单允许 inline，其余类型强制 attachment；所有
文件与图片响应都带 `X-Content-Type-Options: nosniff` 与 `Content-Security-Policy:
sandbox`，避免上传的 HTML 等同源执行脚本（存储型 XSS）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

from botocore.exceptions import ClientError
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, RangeNotSatisfiableError, StorageError
from app.core.storage import Storage, doc_file_key, images_prefix, kb_original_key
from app.iam.context import Ctx
from app.kb.tables import Document
from app.resources import access

# 流式返回的块大小，避免把整份文件读进内存。
_CHUNK_SIZE = 64 * 1024

# 扩展名 → Content-Type；未收录的按二进制流。纯文本类型单独处理为 text/plain。
_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html",
    "htm": "text/html",
    "epub": "application/epub+zip",
    "ofd": "application/ofd",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}

# 纯文本一律以 text/plain; charset=utf-8 返回（不用 text/html 等可执行类型）。
_TEXT_EXTENSIONS = {"txt", "csv", "tsv", "md", "markdown"}
_TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"

# 只有这些扩展名允许 inline；其余（html/htm、Office、epub、ofd 等）无论 download
# 参数如何都强制 attachment，避免存储型 XSS 以平台源执行。
_INLINE_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "bmp",
    "webp",
    "tif",
    "tiff",
    *_TEXT_EXTENSIONS,
}

# 文件与图片响应都加的防 XSS 响应头。
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox",
}

# 只接受单段 `bytes=start-end`（含 `bytes=start-`、`bytes=-suffix`）。
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
# 图片名只允许文件名本身，防止路径穿越。
_IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}
_INVALID_RANGE_CODES = {"416", "InvalidRange", "RequestedRangeNotSatisfiable"}


def content_type_for(ext: str) -> str:
    """按扩展名取 Content-Type；纯文本一律 text/plain。"""
    key = ext.lower()
    if key in _TEXT_EXTENSIONS:
        return _TEXT_CONTENT_TYPE
    return _CONTENT_TYPES.get(key, "application/octet-stream")


def is_inline_allowed(ext: str) -> bool:
    """该扩展名是否允许 inline（白名单外的类型一律下载）。"""
    return ext.lower() in _INLINE_EXTENSIONS


def _client_error(exc: StorageError) -> ClientError | None:
    cause = exc.__cause__
    return cause if isinstance(cause, ClientError) else None


def _error_code(exc: StorageError) -> str:
    client_error = _client_error(exc)
    if client_error is None:
        return ""
    return client_error.response.get("Error", {}).get("Code", "") or ""


def _http_status(exc: StorageError) -> int | None:
    client_error = _client_error(exc)
    if client_error is None:
        return None
    return client_error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")


def is_not_found(exc: StorageError) -> bool:
    """对象不存在（StorageError 的 cause 是 S3 404）。"""
    return _error_code(exc) in _NOT_FOUND_CODES or _http_status(exc) == 404


def is_invalid_range(exc: StorageError) -> bool:
    """Range 越界（StorageError 的 cause 是 S3 416）。"""
    return _error_code(exc) in _INVALID_RANGE_CODES or _http_status(exc) == 416


def parse_range(header: str | None) -> str | None:
    """解析 HTTP Range。

    只接受单段 `bytes=start-end`；多段（含逗号）、非法格式、`bytes=-` 一律返回
    `None`，调用方按 200 全量返回（P3-14 补充）。
    """
    if not header:
        return None
    header = header.strip()
    if "," in header:
        return None
    match = _RANGE_RE.match(header)
    if match is None:
        return None
    if match.group(1) == "" and match.group(2) == "":
        return None
    return header


def is_safe_image_name(name: str) -> bool:
    """`name` 只能是文件名：不含 `/`、`\\`、`..`，且字符集受限。"""
    if not name or ".." in name or "/" in name or "\\" in name:
        return False
    return _IMAGE_NAME_RE.match(name) is not None


async def get_document(session: AsyncSession, ctx: Ctx, doc_id: int) -> Document:
    """取文档并按知识库“查看”权限判断；看不到的知识库返回 404。"""
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise NotFoundError()
    await access.get_for(session, ctx, doc.kb_id, access.VIEW, type="kb")
    return doc


def _content_disposition(name: str, download: bool, inline_allowed: bool) -> str:
    disposition = "inline" if inline_allowed and not download else "attachment"
    return f"{disposition}; filename*=UTF-8''{quote(name)}"


def _file_name(doc: Document, variant: str) -> str:
    if variant == "preview":
        stem = doc.name.rsplit(".", 1)[0] if "." in doc.name else doc.name
        return f"{stem}.pdf"
    return doc.name


def _iter_body(body: Any) -> Iterator[bytes]:
    """同步生成器，由 Starlette 放到线程池里迭代，不阻塞事件循环。"""
    try:
        while True:
            chunk = body.read(_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        close = getattr(body, "close", None)
        if close is not None:
            close()


async def stream_document_file(
    session: AsyncSession,
    ctx: Ctx,
    storage: Storage,
    doc_id: int,
    variant: str,
    download: bool,
    range_header: str | None,
) -> StreamingResponse:
    """读原文件或预览 PDF，支持 HTTP Range。"""
    doc = await get_document(session, ctx, doc_id)
    if variant == "preview":
        key = doc_file_key(doc.kb_id, doc.id, "preview.pdf")
        content_type = "application/pdf"
        inline_allowed = True
        missing = "该文档没有可预览的 PDF，可下载原文件或查看解析结果"
    else:
        key = kb_original_key(doc.kb_id, doc.id, doc.ext)
        content_type = content_type_for(doc.ext)
        inline_allowed = is_inline_allowed(doc.ext)
        missing = "该文档的原文件不存在"

    byte_range = parse_range(range_header)
    try:
        body, content_length, content_range = await storage.get_object(key, byte_range=byte_range)
    except StorageError as exc:
        if is_not_found(exc):
            raise NotFoundError(missing) from exc
        if byte_range is not None and is_invalid_range(exc):
            raise RangeNotSatisfiableError() from exc
        raise

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": _content_disposition(
            _file_name(doc, variant), download, inline_allowed
        ),
        **_SECURITY_HEADERS,
    }
    status_code = 200
    if byte_range is not None and content_range:
        status_code = 206
        headers["Content-Range"] = content_range
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return StreamingResponse(
        _iter_body(body),
        status_code=status_code,
        media_type=content_type,
        headers=headers,
    )


async def read_document_image(
    session: AsyncSession,
    ctx: Ctx,
    storage: Storage,
    doc_id: int,
    name: str,
) -> Response:
    """读解析出的图片：`kb/{kb_id}/docs/{doc_id}/images/{name}.png`。"""
    doc = await get_document(session, ctx, doc_id)
    if not is_safe_image_name(name):
        raise NotFoundError("图片不存在")
    key = f"{images_prefix(doc.kb_id, doc.id)}{name}.png"
    try:
        data = await storage.get_bytes(key)
    except StorageError as exc:
        if is_not_found(exc):
            raise NotFoundError("图片不存在") from exc
        raise
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600", **_SECURITY_HEADERS},
    )
