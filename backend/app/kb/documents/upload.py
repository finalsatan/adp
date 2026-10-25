"""文档上传与预检（P3-07，知识库技术方案 8.1、9.4、11.1、14）。

- 预检只读：按扩展名取文件类型、检查大小上限、解析方案、同名、内容重复与超长。
- 上传：整批校验（扩展名 / 真实类型 / 大小 / 方案），逐文件按 `resolutions` 处理同名与
  重复，按相对路径在 `folder_id` 下建目录，流式写对象存储并算 SHA-256，建排队中的文档，
  提交后投递 P3-08 的 `dispatch_kb` 调度。

业务逻辑都放这里，路由只做参数接收、权限依赖与调用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
from datetime import UTC, datetime
from typing import Any, BinaryIO
from uuid import uuid4

import filetype
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    FileTooLargeError,
    NotFoundError,
    SchemeUnavailableError,
    UnsupportedFileTypeError,
    ValidationError,
)
from app.core.schemas import UserBrief
from app.core.storage import Storage, kb_original_key
from app.iam.context import Ctx
from app.kb import schemes
from app.kb.schemas import (
    DocumentOut,
    DocumentPrecheckIn,
    DocumentPrecheckOut,
    DocumentRef,
    DocumentStatus,
    DocumentUploadOut,
    FileType,
    FolderBrief,
    PrecheckError,
    PrecheckItem,
    SchemeBrief,
    SchemeConfig,
    SkippedItem,
)
from app.kb.tables import Document, IngestScheme, KbFolder, KnowledgeBase
from app.models.tables import Model
from app.resources import access
from app.resources.tables import Resource
from app.settings import service as settings_service
from app.worker.dispatch import queued_position

logger = logging.getLogger(__name__)

KB_TYPE = "kb"

# 扩展名 → 文件类型，与解析服务 `parser/app/filetypes.py` 的 FILE_TYPES 一致（本卡补充）。
FILE_TYPE_EXTENSIONS: dict[FileType, tuple[str, ...]] = {
    FileType.pdf: ("pdf",),
    FileType.word: ("doc", "docx", "rtf", "odt"),
    FileType.ppt: ("ppt", "pptx", "odp"),
    FileType.excel: ("xls", "xlsx", "ods"),
    FileType.csv: ("csv", "tsv"),
    FileType.markdown: ("md",),
    FileType.txt: ("txt",),
    FileType.html: ("html", "htm", "shtml", "mhtml", "mht"),
    FileType.epub: ("epub",),
    FileType.ofd: ("ofd",),
    FileType.image: ("png", "jpg", "jpeg", "bmp", "tiff", "webp", "gif", "jp2"),
}

EXT_TO_FILE_TYPE: dict[str, FileType] = {
    ext: file_type for file_type, extensions in FILE_TYPE_EXTENSIONS.items() for ext in extensions
}

# 超长预检的两项预留（知识库技术方案 9.4）。
TITLE_PATH_RESERVED_TOKENS = 100
CONTEXT_RESERVED_TOKENS = 100
# 与向量化模型最大输入比较的比例（9.4）。
MAX_INPUT_RATIO = 0.9


def _ext(name: str) -> str:
    """取小写扩展名（不含点）；没有扩展名返回空串。"""
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


def _basename(name: str) -> str:
    """去掉浏览器可能带的路径，只保留文件名。"""
    return posixpath.basename(name.replace("\\", "/").strip())


def file_type_for_name(name: str) -> FileType | None:
    """按扩展名取文件类型；不支持返回 None。"""
    return EXT_TO_FILE_TYPE.get(_ext(name))


def _scheme_max_tokens(config: SchemeConfig) -> int:
    """方案最大长度：开启父子分块时取子块最大长度（知识库技术方案 9.4）。"""
    if config.chunk.parent_child.enabled:
        return config.chunk.parent_child.child_max_tokens
    return config.chunk.max_tokens


def _too_long(config: SchemeConfig, embedding_max_input: int) -> tuple[bool, int, int]:
    """返回（是否超长、估算 tokens、上限 tokens）。"""
    estimated = _scheme_max_tokens(config) + TITLE_PATH_RESERVED_TOKENS + CONTEXT_RESERVED_TOKENS
    limit = int(embedding_max_input * MAX_INPUT_RATIO)
    return estimated > limit, estimated, limit


def _parse_json_object(raw: str | None, field: str) -> dict[str, Any]:
    if raw is None or raw.strip() == "":
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{field} 不是合法 JSON", {"field": field}) from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{field} 必须是 JSON 对象", {"field": field})
    return value


def _nested_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key) or {}
    if not isinstance(value, dict):
        raise ValidationError(f"{key} 必须是 JSON 对象", {"field": key})
    return value


class _HashingReader:
    """只读包装：`Storage.upload_stream` 读取时顺带更新 SHA-256，不把文件读进内存。

    boto3 为了算校验和 / 分片会把同一个文件读多次（还会 seek 回去重读），所以按读取的
    绝对位置只对“还没哈希过”的区间更新，重复读取忽略，出现空洞时先补读空洞（文件在本地
    临时文件上，补读不产生网络开销）。
    """

    def __init__(self, fileobj: BinaryIO) -> None:
        self._file = fileobj
        self._hash = hashlib.sha256()
        self._hashed = 0

    def read(self, size: int = -1) -> bytes:
        position = self._file.tell()
        data = self._file.read(size)
        if data:
            self._consume(position, data)
        return data

    def _consume(self, position: int, data: bytes) -> None:
        if position < self._hashed:
            return
        if position > self._hashed:
            # 空洞：先按顺序补读 [_hashed, position) 再继续。
            current = self._file.tell()
            self._file.seek(self._hashed)
            gap = self._file.read(position - self._hashed)
            self._hash.update(gap)
            self._hashed += len(gap)
            self._file.seek(current)
        if position == self._hashed:
            self._hash.update(data)
            self._hashed += len(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        return self._file.tell()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._file, name)

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()


def _validate_real_type(fileobj: BinaryIO, file_type: FileType) -> None:
    """按文件头校验真实类型（知识库技术方案 6.4）。

    `filetype` 认不出（纯文本等）时不拒绝；认出的扩展名与所选文件类型不符才拒绝。
    """
    fileobj.seek(0)
    guessed = filetype.guess(fileobj)
    fileobj.seek(0)
    if guessed is None:
        return
    if guessed.extension not in FILE_TYPE_EXTENSIONS[file_type]:
        raise UnsupportedFileTypeError(
            detail={"guessed": guessed.extension, "expected": file_type.value}
        )


async def _load_usable_scheme(
    session: AsyncSession, scheme_id: int, file_type: FileType
) -> IngestScheme | None:
    """按 ID 取方案并校验类型与可用性；不可用返回 None。"""
    scheme = await session.get(IngestScheme, scheme_id)
    if scheme is None or scheme.file_type != file_type.value:
        return None
    if scheme.resource_id is not None:
        resource = await session.get(Resource, scheme.resource_id)
        if resource is None or resource.deleted_at is not None or resource.status != "enabled":
            return None
    return scheme


async def _resolve_scheme(
    session: AsyncSession,
    kb_id: int,
    file_type: FileType,
    requested_id: int | None,
) -> IngestScheme | None:
    """指定方案优先，其次知识库默认方案，最后该类型内置方案（P3-06）。"""
    if requested_id is not None:
        return await _load_usable_scheme(session, requested_id, file_type)
    resolved_id = await schemes.resolve_scheme_id(session, kb_id, file_type)
    if resolved_id is None:
        return None
    return await _load_usable_scheme(session, resolved_id, file_type)


async def _find_by_name(
    session: AsyncSession, kb_id: int, folder_id: int | None, name: str
) -> Document | None:
    stmt = sa.select(Document).where(Document.kb_id == kb_id, Document.name == name)
    if folder_id is None:
        stmt = stmt.where(Document.folder_id.is_(None))
    else:
        stmt = stmt.where(Document.folder_id == folder_id)
    return (await session.execute(stmt.order_by(Document.id).limit(1))).scalar_one_or_none()


async def _find_by_sha256(session: AsyncSession, kb_id: int, sha256: str) -> Document | None:
    return (
        await session.execute(
            sa.select(Document)
            .where(Document.kb_id == kb_id, Document.sha256 == sha256)
            .order_by(Document.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def precheck_documents(
    session: AsyncSession,
    ctx: Ctx,
    kb_id: int,
    body: DocumentPrecheckIn,
) -> DocumentPrecheckOut:
    """上传预检：只读，不写库、不改状态。"""
    await access.get_for(session, ctx, kb_id, access.EDIT, type=KB_TYPE)
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError()
    model = await session.get(Model, kb.embedding_model_id)
    embedding_max_input = int(model.embedding_max_input or 0) if model is not None else 0

    settings = await settings_service.get_settings(session)
    max_size = settings.upload.max_file_size_mb * 1024 * 1024

    folder_id = body.folder_id
    if folder_id is not None:
        folder = await session.get(KbFolder, folder_id)
        if folder is None or folder.kb_id != kb_id:
            raise NotFoundError()

    items: list[PrecheckItem] = []
    errors: list[PrecheckError] = []
    for entry in body.files:
        file_type = file_type_for_name(entry.name)
        if file_type is None:
            errors.append(
                PrecheckError(
                    name=entry.name,
                    code="UNSUPPORTED_FILE_TYPE",
                    message="不支持的文件类型",
                )
            )
            continue
        if entry.size > max_size:
            errors.append(
                PrecheckError(name=entry.name, code="FILE_TOO_LARGE", message="文件超过大小上限")
            )
            continue
        scheme = await _resolve_scheme(session, kb_id, file_type, body.schemes.get(file_type.value))
        if scheme is None:
            errors.append(
                PrecheckError(name=entry.name, code="SCHEME_UNAVAILABLE", message="入库方案不可用")
            )
            continue

        config = SchemeConfig.model_validate(scheme.config)
        too_long, estimated_tokens, limit_tokens = _too_long(config, embedding_max_input)
        conflict = await _find_by_name(session, kb_id, folder_id, entry.name)
        duplicate = await _find_by_sha256(session, kb_id, entry.sha256)
        items.append(
            PrecheckItem(
                name=entry.name,
                relative_path=entry.relative_path,
                file_type=file_type,
                size=entry.size,
                scheme_id=scheme.id,
                scheme_name=scheme.name,
                name_conflict=(
                    DocumentRef(doc_id=conflict.id, name=conflict.name)
                    if conflict is not None
                    else None
                ),
                duplicate=(
                    DocumentRef(doc_id=duplicate.id, name=duplicate.name)
                    if duplicate is not None
                    else None
                ),
                too_long=too_long,
                estimated_tokens=estimated_tokens,
                limit_tokens=limit_tokens,
            )
        )
    return DocumentPrecheckOut(items=items, errors=errors)


async def _get_or_create_folder(
    session: AsyncSession,
    kb_id: int,
    parent: KbFolder | None,
    name: str,
) -> KbFolder:
    """按同级名称取文件夹，没有就建一个（不提交，物化路径与 P3-05 一致）。"""
    parent_id = parent.id if parent is not None else None
    stmt = sa.select(KbFolder).where(KbFolder.kb_id == kb_id, KbFolder.name == name)
    if parent_id is None:
        stmt = stmt.where(KbFolder.parent_id.is_(None))
    else:
        stmt = stmt.where(KbFolder.parent_id == parent_id)
    existing = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if existing is not None:
        return existing

    folder = KbFolder(kb_id=kb_id, parent_id=parent_id, path="", name=name)
    session.add(folder)
    await session.flush()
    folder.path = (parent.path if parent is not None else "/") + f"{folder.id}/"
    # 立刻把 path 落库，避免同一事务里出现第二个 path='' 触发唯一索引冲突。
    await session.flush()
    return folder


async def _resolve_target_folder(
    session: AsyncSession,
    kb_id: int,
    base_folder: KbFolder | None,
    relative_path: str | None,
    cache: dict[tuple[int | None, str], KbFolder],
) -> KbFolder | None:
    """`relative_path` 的目录部分在 `base_folder` 下 `mkdir -p`，只建文档所在的目录。"""
    directory = posixpath.dirname((relative_path or "").replace("\\", "/"))
    if not directory:
        return base_folder
    current = base_folder
    for segment in directory.split("/"):
        if segment in ("", ".", ".."):
            continue
        key = (current.id if current is not None else None, segment)
        folder = cache.get(key)
        if folder is None:
            folder = await _get_or_create_folder(session, kb_id, current, segment)
            cache[key] = folder
        current = folder
    return current


def _split_name(name: str) -> tuple[str, str]:
    if "." not in name:
        return name, ""
    base, _, ext = name.rpartition(".")
    return base, f".{ext}"


async def _keep_name(session: AsyncSession, kb_id: int, folder_id: int | None, name: str) -> str:
    """保留两份：`原名(1).扩展名`，序号从 1 递增取第一个不冲突的（2026-10-25 用户决定）。"""
    base, ext = _split_name(name)
    index = 1
    while True:
        candidate = f"{base}({index}){ext}"
        if await _find_by_name(session, kb_id, folder_id, candidate) is None:
            return candidate
        index += 1


def _dispatch(kb_id: int) -> None:
    """投递 P3-08 的调度任务；按任务名投递，避免和 P3-08 互相 import。"""
    from app.worker.celery_app import celery_app

    celery_app.send_task("app.worker.tasks.dispatch_kb", args=[kb_id], queue="ingest")


def _document_out(
    doc: Document,
    folder: KbFolder | None,
    scheme: IngestScheme | None,
    user: UserBrief,
    queue_position: int | None,
) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        kb_id=doc.kb_id,
        folder=(
            FolderBrief(id=folder.id, name=folder.name, path=folder.path)
            if folder is not None
            else None
        ),
        name=doc.name,
        file_type=FileType(doc.file_type),
        ext=doc.ext,
        size=doc.size,
        status=DocumentStatus(doc.status),
        step=doc.step,
        progress=doc.progress,
        failed_step=doc.failed_step,
        error_code=doc.error_code,
        error_message=doc.error_message,
        queued_at=doc.queued_at,
        started_at=doc.started_at,
        finished_at=doc.finished_at,
        heartbeat_at=doc.heartbeat_at,
        scheme=(
            SchemeBrief(
                id=scheme.id,
                name=scheme.name,
                file_type=FileType(scheme.file_type),
                builtin=scheme.builtin,
            )
            if scheme is not None
            else None
        ),
        scheme_snapshot_name=(doc.parse_snapshot or {}).get("name"),
        current_generation=doc.current_generation,
        metadata=doc.doc_metadata,
        enabled=doc.enabled,
        page_count=doc.page_count,
        chunk_count=doc.chunk_count,
        enhance_partial_failed=doc.enhance_partial_failed,
        edited=False,
        uploaded_by=user,
        run_by=user,
        queue_position=queue_position,
        created_at=doc.created_at,
    )


async def upload_documents(
    session: AsyncSession,
    ctx: Ctx,
    kb_id: int,
    files: list[Any],
    relative_paths: list[str],
    folder_id: int | None,
    schemes_raw: str | None,
    resolutions_raw: str | None,
    *,
    storage: Storage,
) -> DocumentUploadOut:
    """上传文档：整批校验后逐文件落库、写对象存储，提交后投递调度。"""
    await access.get_for(session, ctx, kb_id, access.EDIT, type=KB_TYPE)
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError()

    if len(relative_paths) != len(files):
        raise ValidationError(
            "relative_paths 与 files 数量不一致",
            {"files": len(files), "relative_paths": len(relative_paths)},
        )

    settings = await settings_service.get_settings(session)
    max_size = settings.upload.max_file_size_mb * 1024 * 1024

    schemes_map = _parse_json_object(schemes_raw, "schemes")
    resolutions = _parse_json_object(resolutions_raw, "resolutions")
    name_resolutions = _nested_object(resolutions, "name_conflict")
    duplicate_resolutions = _nested_object(resolutions, "duplicate")

    base_folder: KbFolder | None = None
    if folder_id is not None:
        base_folder = await session.get(KbFolder, folder_id)
        if base_folder is None or base_folder.kb_id != kb_id:
            raise NotFoundError()

    # 整批校验：类型 / 真实类型 / 大小 / 方案，有一个不合格整批拒绝。
    prepared: list[tuple[Any, str, str, int, FileType, IngestScheme]] = []
    for upload, relative_path in zip(files, relative_paths, strict=True):
        name = _basename(upload.filename or "")
        file_type = file_type_for_name(name)
        if file_type is None:
            raise UnsupportedFileTypeError(detail={"name": name})
        size = upload.size if upload.size is not None else _file_size(upload.file)
        if size > max_size:
            raise FileTooLargeError(detail={"name": name, "size": size})
        _validate_real_type(upload.file, file_type)
        scheme = await _resolve_scheme(session, kb_id, file_type, schemes_map.get(file_type.value))
        if scheme is None:
            raise SchemeUnavailableError(detail={"name": name, "file_type": file_type.value})
        prepared.append((upload, relative_path, name, size, file_type, scheme))

    folder_cache: dict[tuple[int | None, str], KbFolder] = {}
    created: list[tuple[int, int | None, int]] = []
    skipped: list[SkippedItem] = []

    for upload, relative_path, name, size, file_type, scheme in prepared:
        # 内容重复：用户确认过的处理方式，skip 直接跳过，不写库、不写对象存储、不建目录。
        if duplicate_resolutions.get(name) == "skip":
            skipped.append(SkippedItem(name=name, reason="内容重复"))
            continue

        target_folder = await _resolve_target_folder(
            session, kb_id, base_folder, relative_path, folder_cache
        )
        target_folder_id = target_folder.id if target_folder is not None else None

        conflict = await _find_by_name(session, kb_id, target_folder_id, name)
        replace_doc: Document | None = None
        if conflict is not None and name_resolutions.get(name) == "replace":
            replace_doc = conflict
            doc = replace_doc
        else:
            final_name = name
            if conflict is not None:
                final_name = await _keep_name(session, kb_id, target_folder_id, name)
            doc = Document(
                kb_id=kb_id,
                folder_id=target_folder_id,
                name=final_name,
                file_type=file_type.value,
                ext=_ext(final_name),
                size=0,
                sha256="",
                status="queued",
                queued_at=datetime.now(UTC),
                run_id=str(uuid4()),
                scheme_id=scheme.id,
                doc_metadata={},
                enabled=True,
                uploaded_by=ctx.user_id,
                run_by=ctx.user_id,
            )
            session.add(doc)
            await session.flush()

        ext = _ext(name)
        key = kb_original_key(kb_id, doc.id, ext)
        reader = _HashingReader(upload.file)
        upload.file.seek(0)
        await storage.upload_stream(key, reader, content_type=upload.content_type)

        now = datetime.now(UTC)
        doc.file_type = file_type.value
        doc.ext = ext
        doc.size = size
        doc.sha256 = reader.sha256
        doc.status = "queued"
        doc.step = None
        doc.progress = 0
        doc.failed_step = None
        doc.error_code = None
        doc.error_message = None
        doc.queued_at = now
        doc.started_at = None
        doc.finished_at = None
        doc.heartbeat_at = None
        doc.run_id = str(uuid4())
        doc.scheme_id = scheme.id
        doc.parse_snapshot = None
        doc.chunk_snapshot = None
        doc.enabled = True
        doc.uploaded_by = ctx.user_id
        doc.run_by = ctx.user_id
        doc.updated_at = now
        if replace_doc is not None:
            # 换新内容，旧的解析产物与计数作废，等流水线重新生成。
            doc.page_count = None
            doc.chunk_count = 0
            doc.enhance_partial_failed = False
        created.append((doc.id, doc.folder_id, scheme.id))

    await session.commit()
    if created:
        _dispatch(kb_id)

    items = await _build_items(session, ctx, created)
    return DocumentUploadOut(items=items, skipped=skipped)


def _file_size(fileobj: BinaryIO) -> int:
    position = fileobj.tell()
    fileobj.seek(0, 2)
    size = fileobj.tell()
    fileobj.seek(position)
    return size


async def _build_items(
    session: AsyncSession,
    ctx: Ctx,
    created: list[tuple[int, int | None, int]],
) -> list[DocumentOut]:
    """提交后重新读文档、文件夹、方案，拼返回结构（顺带算排队位次）。"""
    if not created:
        return []
    doc_ids = [doc_id for doc_id, _, _ in created]
    docs = {
        doc.id: doc
        for doc in (await session.execute(sa.select(Document).where(Document.id.in_(doc_ids))))
        .scalars()
        .all()
    }
    folder_ids = {folder_id for _, folder_id, _ in created if folder_id is not None}
    folders = (
        {
            folder.id: folder
            for folder in (
                await session.execute(sa.select(KbFolder).where(KbFolder.id.in_(folder_ids)))
            )
            .scalars()
            .all()
        }
        if folder_ids
        else {}
    )
    scheme_ids = {scheme_id for _, _, scheme_id in created}
    scheme_rows = {
        scheme.id: scheme
        for scheme in (
            await session.execute(sa.select(IngestScheme).where(IngestScheme.id.in_(scheme_ids)))
        )
        .scalars()
        .all()
    }
    user = UserBrief(id=ctx.user_id, username=ctx.username, name=ctx.name)
    items: list[DocumentOut] = []
    for doc_id, folder_id, scheme_id in created:
        doc = docs[doc_id]
        items.append(
            _document_out(
                doc,
                folders.get(folder_id),
                scheme_rows.get(scheme_id),
                user,
                await queued_position(session, doc),
            )
        )
    return items
