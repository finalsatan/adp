"""文档列表、详情、编辑、重试、重新解析、删除与批量操作（P3-12）。

业务逻辑都放这里，路由只做参数接收、权限依赖、调用本模块和写审计。
权限一律复用 `app/resources/access.py`（P1-12）：查看用 `access.VIEW`，
编辑、重试、重新解析、删除、批量用 `access.EDIT`。

重新解析 / 重试把断点写进 `documents.step`（1 转换、2 解析、3 分块、5 向量化、
6 生效），流水线（P3-09~P3-11）据此从断点继续、不重复已完成的步骤（知识库技术方案
8.3）。第三期没有解析结果 / 分块编辑，`edited` 恒为 false，重新解析的人工修改计数
也恒为 0，字段照常返回（P3-12 补充）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    DocumentNotReadyError,
    MetadataValueInvalidError,
    NotFoundError,
    SchemeUnavailableError,
    ValidationError,
)
from app.core.schemas import Pagination, UserBrief
from app.core.storage import Storage
from app.iam.context import Ctx
from app.iam.tables import User
from app.kb import indexing
from app.kb import metadata as metadata_service
from app.kb.documents import upload as upload_service
from app.kb.index_table import index_table_exists, index_table_name
from app.kb.schemas import (
    BatchFailure,
    DocumentBatchAction,
    DocumentBatchIn,
    DocumentBatchOut,
    DocumentDetailOut,
    DocumentOut,
    DocumentPage,
    DocumentParseOut,
    DocumentStatus,
    DocumentUpdate,
    FileType,
    FolderBrief,
    MetadataFieldType,
    MetadataFilter,
    MetadataOperator,
    ParseRecordOut,
    ReparseIn,
    ReparseMode,
    ReparseOut,
    SchemeBrief,
)
from app.kb.tables import (
    Chunk,
    Document,
    DocumentParse,
    IngestScheme,
    KbFolder,
    KbMetadataField,
    ParseRecord,
)
from app.resources import access
from app.worker.dispatch import PROCESSING_STATUSES, queued_position

logger = logging.getLogger(__name__)

KB_TYPE = "kb"

ALL_STATUSES = [status.value for status in DocumentStatus]

# 重新解析 / 重试的断点步骤（知识库技术方案 8.1）。
STEP_CONVERT = 1
STEP_CHUNK = 3

# 数字字段过滤用的 SQL 比较符（与 P3-13 的检索过滤一致）。
_NUMERIC_OPS = {
    MetadataOperator.eq: "=",
    MetadataOperator.ne: "<>",
    MetadataOperator.gt: ">",
    MetadataOperator.gte: ">=",
    MetadataOperator.lt: "<",
    MetadataOperator.lte: "<=",
}


@dataclass
class DeletedDocument:
    """删除文档后交给路由写审计用的信息（行已删除，不能再读 ORM 对象）。"""

    doc_id: int
    name: str
    kb_id: int
    org_id: int | None


@dataclass
class BatchOutcome:
    """批量操作结果 + 写审计需要的信息。"""

    result: DocumentBatchOut
    kb_id: int | None = None
    org_id: int | None = None
    deleted: list[int] = field(default_factory=list)


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _now() -> datetime:
    return datetime.now(UTC)


async def _get_doc(session: AsyncSession, ctx: Ctx, doc_id: int, action: str) -> Document:
    """取文档并按知识库资源权限判断；看不到的知识库返回 404。"""
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise NotFoundError()
    await access.get_for(session, ctx, doc.kb_id, action, type=KB_TYPE)
    return doc


# ---- 返回结构 ----


def _document_out(
    doc: Document,
    folder: KbFolder | None,
    scheme: IngestScheme | None,
    users: dict[int, UserBrief],
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
        # 第三期没有解析结果 / 分块编辑，人工修改恒为 false（P3-12 补充）。
        edited=False,
        uploaded_by=users[doc.uploaded_by],
        run_by=users[doc.run_by],
        queue_position=queue_position,
        created_at=doc.created_at,
    )


async def _load_users(session: AsyncSession, user_ids: set[int]) -> dict[int, UserBrief]:
    if not user_ids:
        return {}
    rows = (await session.execute(sa.select(User).where(User.id.in_(user_ids)))).scalars().all()
    return {user.id: UserBrief(id=user.id, username=user.username, name=user.name) for user in rows}


async def _load_outs(session: AsyncSession, docs: list[Document]) -> list[DocumentOut]:
    """批量拼返回结构，避免逐条查文件夹 / 方案 / 用户。"""
    if not docs:
        return []
    folder_ids = {doc.folder_id for doc in docs if doc.folder_id is not None}
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
    scheme_ids = {doc.scheme_id for doc in docs if doc.scheme_id is not None}
    schemes = (
        {
            scheme.id: scheme
            for scheme in (
                await session.execute(
                    sa.select(IngestScheme).where(IngestScheme.id.in_(scheme_ids))
                )
            )
            .scalars()
            .all()
        }
        if scheme_ids
        else {}
    )
    user_ids = {doc.uploaded_by for doc in docs} | {doc.run_by for doc in docs}
    users = await _load_users(session, user_ids)

    outs: list[DocumentOut] = []
    for doc in docs:
        outs.append(
            _document_out(
                doc,
                folders.get(doc.folder_id),
                schemes.get(doc.scheme_id),
                users,
                await queued_position(session, doc),
            )
        )
    return outs


async def _status_counts(session: AsyncSession, kb_id: int) -> dict[str, int]:
    """本知识库全部文档按状态计数，不受分页和筛选影响（P3-12 补充）。"""
    rows = await session.execute(
        sa.select(Document.status, sa.func.count())
        .where(Document.kb_id == kb_id)
        .group_by(Document.status)
    )
    counts = dict.fromkeys(ALL_STATUSES, 0)
    for status, count in rows:
        counts[str(status)] = int(count)
    return counts


# ---- 元数据过滤与校验 ----


def _coerce(field: KbMetadataField, value: Any) -> Any:
    """按字段类型转换过滤值（前端统一按字符串提交）。"""
    if value is None:
        return None
    if field.type == MetadataFieldType.number.value:
        if isinstance(value, bool):
            raise MetadataValueInvalidError(detail={"field": field.name, "reason": "不是数字"})
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise MetadataValueInvalidError(
                detail={"field": field.name, "reason": "不是数字"}
            ) from exc
    if field.type == MetadataFieldType.date.value:
        if not isinstance(value, str):
            raise MetadataValueInvalidError(detail={"field": field.name, "reason": "不是日期"})
        return value
    return str(value)


async def _metadata_clause(
    session: AsyncSession, kb_id: int, leaf: MetadataFilter
) -> sa.ColumnElement[bool]:
    """把单个叶子条件翻成 `documents.metadata` 的 WHERE 条件（P3-01 的 MetadataFilter）。"""
    field = await session.get(KbMetadataField, leaf.field_id)
    if field is None or field.kb_id != kb_id:
        raise ValidationError("元数据字段不存在", {"field_id": leaf.field_id})
    base = Document.doc_metadata.op("->>")(field.name)
    is_number = field.type == MetadataFieldType.number.value
    column = sa.cast(base, sa.Numeric) if is_number else base

    if leaf.operator == MetadataOperator.in_:
        raw = leaf.value if isinstance(leaf.value, list) else [leaf.value]
        values = [_coerce(field, value) for value in raw]
        if not values:
            return sa.false()
        return column.in_(values)
    if leaf.operator == MetadataOperator.contains:
        pattern = f"%{_escape_like(str(leaf.value))}%"
        return base.like(pattern, escape="\\")

    value = _coerce(field, leaf.value)
    if value is None:
        # 值缺失：eq 表示文档没有这个字段，其余条件不匹配。
        return base.is_(None) if leaf.operator == MetadataOperator.eq else sa.false()
    return column.op(_NUMERIC_OPS[leaf.operator])(value)


def _parse_metadata(raw: str | None) -> MetadataFilter | None:
    """解析列表的 `metadata` 查询参数：单个叶子条件（P3-16 的文档列表筛选）。"""
    if raw is None or raw.strip() == "":
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("metadata 不是合法 JSON", {"field": "metadata"}) from exc
    try:
        return MetadataFilter.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationError("metadata 条件不正确", {"field": "metadata"}) from exc


async def _field_names(session: AsyncSession, kb_id: int) -> set[str]:
    return set(
        (
            await session.execute(
                sa.select(KbMetadataField.name).where(KbMetadataField.kb_id == kb_id)
            )
        )
        .scalars()
        .all()
    )


async def _validate_metadata(session: AsyncSession, kb_id: int, metadata: dict[str, Any]) -> None:
    """校验提交的键都是已定义字段、非空值符合字段类型；null 表示清空该键。"""
    if not metadata:
        return
    names = await _field_names(session, kb_id)
    for name in metadata:
        if name not in names:
            raise MetadataValueInvalidError(detail={"field": name, "reason": "字段未定义"})
    to_set = {name: value for name, value in metadata.items() if value is not None}
    await metadata_service.validate_metadata(session, kb_id, to_set)


def _merge_metadata(doc: Document, metadata: dict[str, Any]) -> None:
    """只改提交的键；值为 null 时删除该键。"""
    merged = dict(doc.doc_metadata or {})
    for name, value in metadata.items():
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = value
    doc.doc_metadata = merged


# ---- 列表与详情 ----


async def list_documents(
    session: AsyncSession,
    ctx: Ctx,
    kb_id: int,
    pagination: Pagination,
    *,
    folder_id: int | None,
    include_sub: bool,
    uncategorized: bool,
    status: DocumentStatus | None,
    file_type: FileType | None,
    scheme_id: int | None,
    keyword: str | None,
    metadata: str | None,
    edited: bool | None,
) -> DocumentPage:
    await access.get_for(session, ctx, kb_id, access.VIEW, type=KB_TYPE)
    conditions: list[sa.ColumnElement[bool]] = [Document.kb_id == kb_id]

    if uncategorized:
        conditions.append(Document.folder_id.is_(None))
    elif folder_id is not None:
        folder = await session.get(KbFolder, folder_id)
        if folder is None or folder.kb_id != kb_id:
            raise NotFoundError()
        if include_sub:
            sub = sa.select(KbFolder.id).where(
                KbFolder.kb_id == kb_id,
                KbFolder.path.like(_escape_like(folder.path) + "%", escape="\\"),
            )
            conditions.append(Document.folder_id.in_(sub))
        else:
            conditions.append(Document.folder_id == folder_id)

    if status is not None:
        conditions.append(Document.status == status.value)
    if file_type is not None:
        conditions.append(Document.file_type == file_type.value)
    if scheme_id is not None:
        conditions.append(Document.scheme_id == scheme_id)
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        conditions.append(Document.name.ilike(pattern, escape="\\"))

    leaf = _parse_metadata(metadata)
    if leaf is not None:
        conditions.append(await _metadata_clause(session, kb_id, leaf))

    if edited is not None:
        # 第三期 edited 恒为 false：true 时无结果，false 时全部返回（按字段过滤，未来可用）。
        edited_exists = (
            sa.select(Chunk.id).where(Chunk.doc_id == Document.id, Chunk.edited.is_(True)).exists()
        )
        conditions.append(edited_exists if edited else sa.not_(edited_exists))

    total = int(
        await session.scalar(sa.select(sa.func.count()).select_from(Document).where(*conditions))
        or 0
    )
    docs = list(
        (
            await session.execute(
                sa.select(Document)
                .where(*conditions)
                .order_by(Document.created_at.desc(), Document.id.desc())
                .offset((pagination.page - 1) * pagination.page_size)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )
    return DocumentPage(
        items=await _load_outs(session, docs),
        total=total,
        status_counts=await _status_counts(session, kb_id),
    )


async def get_document_detail(session: AsyncSession, ctx: Ctx, doc_id: int) -> DocumentDetailOut:
    doc = await _get_doc(session, ctx, doc_id, access.VIEW)
    base = (await _load_outs(session, [doc]))[0]

    parse_rows = list(
        (await session.execute(sa.select(DocumentParse).where(DocumentParse.doc_id == doc_id)))
        .scalars()
        .all()
    )
    users = await _load_users(
        session, {row.created_by for row in parse_rows if row.created_by is not None}
    )
    parses = [
        DocumentParseOut(
            kind=row.kind,  # type: ignore[arg-type]
            doc_key=row.doc_key,
            md_key=row.md_key,
            engine=row.engine,
            engine_version=row.engine_version,
            pages=row.pages,
            ocr_pages=row.ocr_pages,
            created_by=users.get(row.created_by) if row.created_by is not None else None,
            created_at=row.created_at,
        )
        for row in parse_rows
    ]
    records = list(
        (
            await session.execute(
                sa.select(ParseRecord)
                .where(ParseRecord.doc_id == doc_id)
                .order_by(ParseRecord.created_at.desc(), ParseRecord.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return DocumentDetailOut(
        **base.model_dump(),
        processing=doc.status in PROCESSING_STATUSES,
        parse_snapshot=doc.parse_snapshot,
        parses=parses,
        parse_records=[
            ParseRecordOut(
                id=row.id,
                engine=row.engine,
                pages=row.pages,
                ocr_pages=row.ocr_pages,
                duration_ms=row.duration_ms,
                ok=row.ok,
                error_code=row.error_code,
                created_at=row.created_at,
            )
            for row in records
        ],
    )


# ---- 编辑、重试、重新解析 ----


async def update_document(
    session: AsyncSession, ctx: Ctx, doc_id: int, body: DocumentUpdate
) -> DocumentOut:
    doc = await _get_doc(session, ctx, doc_id, access.EDIT)

    # folder_id 显式传 null 表示移到未分类；没传则不动（用 model_fields_set 区分）。
    if "folder_id" in body.model_fields_set:
        if body.folder_id is None:
            doc.folder_id = None
        else:
            folder = await session.get(KbFolder, body.folder_id)
            if folder is None or folder.kb_id != doc.kb_id:
                raise NotFoundError()
            doc.folder_id = folder.id

    if body.metadata is not None:
        await _validate_metadata(session, doc.kb_id, body.metadata)
        _merge_metadata(doc, body.metadata)

    if body.enabled is not None:
        doc.enabled = body.enabled
        await _set_doc_active(session, doc.kb_id, doc.id, body.enabled)

    doc.updated_at = _now()
    await session.commit()
    return (await _load_outs(session, [doc]))[0]


def _requeue(doc: Document, ctx: Ctx, *, start_step: int) -> None:
    """把文档改回排队中并换 run_id；`step` 作为流水线的断点（8.3）。"""
    now = _now()
    doc.run_id = str(uuid4())
    doc.status = DocumentStatus.queued.value
    doc.step = start_step
    doc.failed_step = None
    doc.error_code = None
    doc.error_message = None
    doc.progress = 0
    doc.queued_at = now
    doc.started_at = None
    doc.finished_at = None
    doc.heartbeat_at = None
    doc.run_by = ctx.user_id
    doc.updated_at = now


async def retry_document(session: AsyncSession, ctx: Ctx, doc_id: int) -> DocumentOut:
    doc = await _get_doc(session, ctx, doc_id, access.EDIT)
    if doc.status != DocumentStatus.failed.value:
        raise DocumentNotReadyError("只有失败的文档才能重试")
    _requeue(doc, ctx, start_step=doc.failed_step or STEP_CONVERT)
    await session.commit()
    _dispatch_safely(doc.kb_id)
    return (await _load_outs(session, [doc]))[0]


async def _load_usable_scheme(
    session: AsyncSession, scheme_id: int, file_type: FileType
) -> IngestScheme:
    scheme = await upload_service._load_usable_scheme(session, scheme_id, file_type)
    if scheme is None:
        raise SchemeUnavailableError()
    return scheme


async def _prepare_reparse(
    session: AsyncSession, doc: Document, mode: ReparseMode, scheme_id: int | None
) -> None:
    """更新方案快照：重新解析换整套解析 + 分块设置，仅重新分块只换分块设置（8.5）。"""
    if scheme_id is None:
        return
    scheme = await _load_usable_scheme(session, scheme_id, FileType(doc.file_type))
    snapshot = dict(doc.parse_snapshot or {})
    if mode == ReparseMode.reparse:
        doc.parse_snapshot = scheme.config
    else:
        snapshot["chunk"] = (scheme.config or {}).get("chunk")
        doc.parse_snapshot = snapshot
    doc.scheme_id = scheme.id


async def _edited_counts(session: AsyncSession, doc_id: int) -> tuple[bool, int]:
    """人工修改计数：解析结果是否被修改、被修改的分块数（第三期恒为 0）。"""
    parse_edited = (
        await session.scalar(
            sa.select(DocumentParse.doc_id)
            .where(DocumentParse.doc_id == doc_id, DocumentParse.kind == "edited")
            .limit(1)
        )
    ) is not None
    chunk_edited = int(
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(Chunk)
            .where(Chunk.doc_id == doc_id, Chunk.edited.is_(True))
        )
        or 0
    )
    return parse_edited, chunk_edited


async def reparse_document(
    session: AsyncSession, ctx: Ctx, doc_id: int, body: ReparseIn
) -> ReparseOut:
    doc = await _get_doc(session, ctx, doc_id, access.EDIT)
    await _prepare_reparse(session, doc, body.mode, body.scheme_id)
    parse_edited, chunk_edited = await _edited_counts(session, doc_id)
    _requeue(
        doc,
        ctx,
        start_step=STEP_CONVERT if body.mode == ReparseMode.reparse else STEP_CHUNK,
    )
    await session.commit()
    _dispatch_safely(doc.kb_id)
    base = (await _load_outs(session, [doc]))[0]
    return ReparseOut(
        **base.model_dump(),
        parse_edited=parse_edited,
        chunk_edited_count=chunk_edited,
    )


# ---- 删除 ----


async def _set_doc_active(session: AsyncSession, kb_id: int, doc_id: int, enabled: bool) -> None:
    """启停文档时更新索引条目的 `active`；索引表不存在（异常数据）时跳过。"""
    if not await index_table_exists(session, kb_id):
        return
    await indexing.set_doc_active(session, kb_id, doc_id, enabled)


async def _delete_index_entries(session: AsyncSession, kb_id: int, doc_id: int) -> None:
    if not await index_table_exists(session, kb_id):
        return
    await session.execute(
        text(f"DELETE FROM {index_table_name(kb_id)} WHERE doc_id = :doc_id"),
        {"doc_id": doc_id},
    )


async def _delete_documents(
    session: AsyncSession, docs: list[Document], storage: Storage
) -> list[int]:
    """删索引条目、文档行（分块 / 解析结果 / 解析记录级联），再清对象存储前缀。"""
    targets = [(doc.id, doc.kb_id) for doc in docs]
    for doc in docs:
        # 先换 run_id，让正在跑的任务在步骤边界退出（8.3）。
        doc.run_id = str(uuid4())
        await _delete_index_entries(session, doc.kb_id, doc.id)
        await session.delete(doc)
    await session.flush()
    for kb_id in dict.fromkeys(kb_id for _, kb_id in targets):
        await indexing.refresh_kb_counts(session, kb_id)
    await session.commit()

    for doc_id, kb_id in targets:
        # 前缀删除失败只记 ERROR，不让接口 500（P3-04 同思路）。
        try:
            await storage.delete_prefix(f"kb/{kb_id}/docs/{doc_id}/")
        except Exception:
            logger.error("删除文档对象存储前缀失败: doc_id=%s", doc_id, exc_info=True)
    return [doc_id for doc_id, _ in targets]


async def delete_document(
    session: AsyncSession, ctx: Ctx, doc_id: int, *, storage: Storage
) -> DeletedDocument:
    doc = await _get_doc(session, ctx, doc_id, access.EDIT)
    resource = await access.get_for(session, ctx, doc.kb_id, access.EDIT, type=KB_TYPE)
    info = DeletedDocument(doc_id=doc.id, name=doc.name, kb_id=doc.kb_id, org_id=resource.org_id)
    await _delete_documents(session, [doc], storage)
    return info


# ---- 批量操作 ----


async def _load_batch_docs(session: AsyncSession, doc_ids: list[int]) -> list[Document]:
    docs = list(
        (await session.execute(sa.select(Document).where(Document.id.in_(doc_ids)))).scalars().all()
    )
    found = {doc.id for doc in docs}
    missing = [doc_id for doc_id in doc_ids if doc_id not in found]
    if missing:
        raise NotFoundError("部分文档不存在或已删除", {"doc_ids": missing})
    by_id = {doc.id: doc for doc in docs}
    return [by_id[doc_id] for doc_id in doc_ids]


async def batch_documents(
    session: AsyncSession, ctx: Ctx, body: DocumentBatchIn, *, storage: Storage
) -> BatchOutcome:
    docs = await _load_batch_docs(session, body.doc_ids)
    kb_ids = {doc.kb_id for doc in docs}
    if len(kb_ids) != 1:
        raise ValidationError("一次批量只能操作同一知识库的文档")
    kb_id = next(iter(kb_ids))
    resource = await access.get_for(session, ctx, kb_id, access.EDIT, type=KB_TYPE)

    action = body.action
    succeeded: list[int] = []
    failed: list[BatchFailure] = []

    if action == DocumentBatchAction.delete:
        deleted = await _delete_documents(session, docs, storage)
        return BatchOutcome(
            result=DocumentBatchOut(succeeded=deleted, failed=[]),
            kb_id=kb_id,
            org_id=resource.org_id,
            deleted=deleted,
        )

    if action == DocumentBatchAction.set_metadata:
        metadata = body.metadata or {}
        await _validate_metadata(session, kb_id, metadata)
        for doc in docs:
            _merge_metadata(doc, metadata)
            doc.updated_at = _now()
            succeeded.append(doc.id)

    elif action == DocumentBatchAction.move:
        folder = None
        if body.folder_id is not None:
            folder = await session.get(KbFolder, body.folder_id)
            if folder is None or folder.kb_id != kb_id:
                raise NotFoundError()
        for doc in docs:
            doc.folder_id = folder.id if folder is not None else None
            doc.updated_at = _now()
            succeeded.append(doc.id)

    elif action in (DocumentBatchAction.enable, DocumentBatchAction.disable):
        enabled = action == DocumentBatchAction.enable
        for doc in docs:
            doc.enabled = enabled
            doc.updated_at = _now()
            await _set_doc_active(session, kb_id, doc.id, enabled)
            succeeded.append(doc.id)

    elif action == DocumentBatchAction.retry:
        for doc in docs:
            if doc.status != DocumentStatus.failed.value:
                failed.append(
                    BatchFailure(doc_id=doc.id, code="DOCUMENT_NOT_READY", message="不是失败的文档")
                )
                continue
            _requeue(doc, ctx, start_step=doc.failed_step or STEP_CONVERT)
            succeeded.append(doc.id)

    elif action == DocumentBatchAction.reparse:
        mode = body.mode or ReparseMode.reparse
        if body.scheme_id is not None:
            # 先校验一次方案，避免一半文档改了快照、一半报错。
            await _load_usable_scheme(session, body.scheme_id, FileType(docs[0].file_type))
        for doc in docs:
            await _prepare_reparse(session, doc, mode, body.scheme_id)
            _requeue(
                doc, ctx, start_step=STEP_CONVERT if mode == ReparseMode.reparse else STEP_CHUNK
            )
            succeeded.append(doc.id)

    await session.commit()

    if action in (DocumentBatchAction.retry, DocumentBatchAction.reparse) and succeeded:
        _dispatch_safely(kb_id)

    return BatchOutcome(
        result=DocumentBatchOut(succeeded=succeeded, failed=failed),
        kb_id=kb_id,
        org_id=resource.org_id,
    )


# ---- 触发调度 ----


def _dispatch(kb_id: int) -> None:
    """投递 P3-08 的调度任务（与上传一致，按任务名投递避免循环 import）。"""
    from app.worker.celery_app import celery_app

    celery_app.send_task("app.worker.tasks.dispatch_kb", args=[kb_id], queue="ingest")


def _dispatch_safely(kb_id: int) -> None:
    """broker 临时不可用时只记日志；文档已落库，beat 补发会兜底（P3-08）。"""
    try:
        _dispatch(kb_id)
    except Exception:
        logger.warning("投递 dispatch_kb 失败，等 beat 补发: kb_id=%s", kb_id, exc_info=True)
