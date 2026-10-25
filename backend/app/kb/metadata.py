"""知识库元数据字段与取值校验（P3-05）。

取值校验供上传（P3-07）与批量设置元数据（P3-12）复用；过滤条件的 SQL 翻译
（P3-01 的 `MetadataFilter`）在 P3-12 / P3-13 各自实现，不在本文件。
"""

import re
from datetime import date

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    MetadataValueInvalidError,
    NameConflictError,
    NotFoundError,
    ValidationError,
)
from app.iam.context import Ctx
from app.kb.schemas import MetadataFieldOut, MetadataFieldType
from app.kb.tables import Document, KbMetadataField
from app.resources import access

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_OPTIONS = 100


def _normalize_options(options: list[str] | None) -> list[str]:
    """单选字段的选项：非空、去重（保留顺序），1–100 个。"""
    if not options:
        raise ValidationError("单选字段必须提供选项", {"field": "options"})
    normalized: list[str] = []
    for option in options:
        value = option.strip()
        if value and value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValidationError("单选字段必须提供选项", {"field": "options"})
    if len(normalized) > MAX_OPTIONS:
        raise ValidationError(f"单选字段最多 {MAX_OPTIONS} 个选项", {"field": "options"})
    return normalized


def _to_out(field: KbMetadataField, doc_count: int) -> MetadataFieldOut:
    return MetadataFieldOut(
        id=field.id,
        kb_id=field.kb_id,
        name=field.name,
        type=MetadataFieldType(field.type),
        options=field.options,
        doc_count=doc_count,
    )


async def _get_field(
    session: AsyncSession, ctx: Ctx, field_id: int, action: str
) -> KbMetadataField:
    field = await session.get(KbMetadataField, field_id)
    if field is None:
        raise NotFoundError()
    await access.get_for(session, ctx, field.kb_id, action, type="kb")
    return field


async def _ensure_name_free(
    session: AsyncSession, kb_id: int, name: str, exclude_id: int | None = None
) -> None:
    stmt = sa.select(KbMetadataField.id).where(
        KbMetadataField.kb_id == kb_id, KbMetadataField.name == name
    )
    if exclude_id is not None:
        stmt = stmt.where(KbMetadataField.id != exclude_id)
    if await session.scalar(stmt.limit(1)) is not None:
        raise NameConflictError()


async def _doc_count(session: AsyncSession, kb_id: int, name: str) -> int:
    """已被设值的文档数：`documents.metadata ? :name`。"""
    return int(
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(Document)
            .where(Document.kb_id == kb_id, Document.doc_metadata.has_key(name))
        )
        or 0
    )


async def list_fields(session: AsyncSession, ctx: Ctx, kb_id: int) -> list[MetadataFieldOut]:
    await access.get_for(session, ctx, kb_id, access.VIEW, type="kb")
    fields = (
        (
            await session.execute(
                sa.select(KbMetadataField)
                .where(KbMetadataField.kb_id == kb_id)
                .order_by(KbMetadataField.id)
            )
        )
        .scalars()
        .all()
    )
    return [_to_out(field, await _doc_count(session, kb_id, field.name)) for field in fields]


async def create_field(
    session: AsyncSession,
    ctx: Ctx,
    kb_id: int,
    name: str,
    field_type: MetadataFieldType,
    options: list[str] | None,
) -> MetadataFieldOut:
    await access.get_for(session, ctx, kb_id, access.EDIT, type="kb")
    await _ensure_name_free(session, kb_id, name)
    # 只有单选字段存选项；其他类型忽略提交的 options。
    stored_options = _normalize_options(options) if field_type == MetadataFieldType.select else None
    field = KbMetadataField(kb_id=kb_id, name=name, type=field_type.value, options=stored_options)
    session.add(field)
    await session.commit()
    return _to_out(field, 0)


async def update_field(
    session: AsyncSession,
    ctx: Ctx,
    field_id: int,
    name: str | None,
    options: list[str] | None,
) -> MetadataFieldOut:
    """只能改名称和选项，不允许改类型（2026-10-25 用户决定）。"""
    field = await _get_field(session, ctx, field_id, access.EDIT)
    if name is not None and name != field.name:
        await _ensure_name_free(session, field.kb_id, name, exclude_id=field.id)
        # 文档 metadata 以字段名为键，改名要同步迁移旧键，否则已有取值失联。
        await _rename_metadata_key(session, field.kb_id, field.name, name)
        field.name = name
    if options is not None and field.type == MetadataFieldType.select.value:
        field.options = _normalize_options(options)
    await session.commit()
    return _to_out(field, await _doc_count(session, field.kb_id, field.name))


async def _rename_metadata_key(
    session: AsyncSession, kb_id: int, old_name: str, new_name: str
) -> None:
    """把该知识库所有含旧键的文档的 metadata 旧键改成新键，保留值。"""
    metadata_col = Document.doc_metadata
    renamed = metadata_col.op("-")(sa.cast(old_name, sa.Text())).op("||")(
        sa.func.jsonb_build_object(new_name, metadata_col[old_name])
    )
    await session.execute(
        sa.update(Document)
        .where(Document.kb_id == kb_id, metadata_col.has_key(old_name))
        .values(doc_metadata=renamed)
    )


async def delete_field(session: AsyncSession, ctx: Ctx, field_id: int) -> None:
    """删除字段，并在同一事务里从该知识库所有文档的 metadata 里删掉该键。"""
    field = await _get_field(session, ctx, field_id, access.EDIT)
    await session.execute(
        sa.update(Document)
        .where(Document.kb_id == field.kb_id, Document.doc_metadata.has_key(field.name))
        .values(doc_metadata=Document.doc_metadata.op("-")(sa.cast(field.name, sa.Text())))
    )
    await session.delete(field)
    await session.commit()


def _invalid_reason(field: KbMetadataField, value: object) -> str | None:
    """返回不合法的原因；None 表示值合法。"""
    if field.type == MetadataFieldType.text.value:
        if not isinstance(value, str):
            return "文本字段的值必须是字符串"
    elif field.type == MetadataFieldType.number.value:
        # bool 是 int 的子类，单独排除。
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "数字字段的值必须是数字"
    elif field.type == MetadataFieldType.date.value:
        if not isinstance(value, str) or _DATE_RE.match(value) is None:
            return "日期字段的值必须是 YYYY-MM-DD"
        try:
            date.fromisoformat(value)
        except ValueError:
            return "日期字段的值不是有效日期"
    elif field.type == MetadataFieldType.select.value:
        if value not in (field.options or []):
            return "单选字段的值必须是选项之一"
    return None


async def validate_metadata(session: AsyncSession, kb_id: int, metadata: dict) -> None:
    """校验提交的键都是已定义字段、值符合字段类型；不合法抛 422。"""
    if not metadata:
        return
    fields = {
        field.name: field
        for field in (
            await session.execute(sa.select(KbMetadataField).where(KbMetadataField.kb_id == kb_id))
        )
        .scalars()
        .all()
    }
    for name, value in metadata.items():
        field = fields.get(name)
        if field is None:
            raise MetadataValueInvalidError(detail={"field": name, "reason": "字段未定义"})
        reason = _invalid_reason(field, value)
        if reason is not None:
            raise MetadataValueInvalidError(detail={"field": name, "reason": reason})
