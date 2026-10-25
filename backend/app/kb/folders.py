"""知识库文件夹：物化路径与含下级过滤（P3-05）。

物化路径与组织（技术方案 6.1）同一套写法：根文件夹 `/{id}/`，子文件夹为
`{父 path}{id}/`。`folder_scope_clause` 供文档列表（P3-12）与检索（P3-13）复用。
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    FolderHasChildrenError,
    FolderNotEmptyError,
    NameConflictError,
    NotFoundError,
)
from app.iam.context import Ctx
from app.kb.schemas import FolderOut
from app.kb.tables import Document, KbFolder
from app.resources import access


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def folder_scope_clause(folder: KbFolder) -> sa.ColumnElement[bool]:
    """该文件夹含下级的 WHERE 条件。

    `path` 以 `/` 结尾，所以 `LIKE path%` 也会匹配文件夹自身；`/1/%` 不会匹配
    `/10/`（第三个字符不同）。
    """
    return KbFolder.path.like(_escape_like(folder.path) + "%", escape="\\")


def _to_out(folder: KbFolder, doc_count: int) -> FolderOut:
    return FolderOut(
        id=folder.id,
        kb_id=folder.kb_id,
        parent_id=folder.parent_id,
        path=folder.path,
        name=folder.name,
        doc_count=doc_count,
    )


async def _get_folder(session: AsyncSession, ctx: Ctx, folder_id: int, action: str) -> KbFolder:
    folder = await session.get(KbFolder, folder_id)
    if folder is None:
        raise NotFoundError()
    await access.get_for(session, ctx, folder.kb_id, action, type="kb")
    return folder


async def _ensure_name_free(
    session: AsyncSession,
    kb_id: int,
    parent_id: int | None,
    name: str,
    exclude_id: int | None = None,
) -> None:
    """同级重名检查。根文件夹的 parent_id 为空，数据库唯一索引拦不住，这里一并查。"""
    stmt = sa.select(KbFolder.id).where(KbFolder.kb_id == kb_id, KbFolder.name == name)
    if parent_id is None:
        stmt = stmt.where(KbFolder.parent_id.is_(None))
    else:
        stmt = stmt.where(KbFolder.parent_id == parent_id)
    if exclude_id is not None:
        stmt = stmt.where(KbFolder.id != exclude_id)
    if await session.scalar(stmt.limit(1)) is not None:
        raise NameConflictError()


async def _direct_doc_count(session: AsyncSession, folder_id: int) -> int:
    return int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(Document).where(Document.folder_id == folder_id)
        )
        or 0
    )


async def list_folders(session: AsyncSession, ctx: Ctx, kb_id: int) -> list[FolderOut]:
    """按 `path` 排序返回平铺列表，前端拼树。`doc_count` 只算直接包含的文档。"""
    await access.get_for(session, ctx, kb_id, access.VIEW, type="kb")
    folders = (
        (
            await session.execute(
                sa.select(KbFolder).where(KbFolder.kb_id == kb_id).order_by(KbFolder.path)
            )
        )
        .scalars()
        .all()
    )
    counts: dict[int, int] = {}
    if folders:
        rows = await session.execute(
            sa.select(Document.folder_id, sa.func.count())
            .where(Document.folder_id.in_([folder.id for folder in folders]))
            .group_by(Document.folder_id)
        )
        counts = {folder_id: count for folder_id, count in rows}
    return [_to_out(folder, counts.get(folder.id, 0)) for folder in folders]


async def create_folder(
    session: AsyncSession,
    ctx: Ctx,
    kb_id: int,
    parent_id: int | None,
    name: str,
) -> FolderOut:
    await access.get_for(session, ctx, kb_id, access.EDIT, type="kb")
    parent: KbFolder | None = None
    if parent_id is not None:
        parent = await session.get(KbFolder, parent_id)
        if parent is None or parent.kb_id != kb_id:
            raise NotFoundError()
    await _ensure_name_free(session, kb_id, parent_id, name)

    # path 要先拿到自增 id 才能拼：先 flush，再在同一事务里写 path。
    folder = KbFolder(kb_id=kb_id, parent_id=parent_id, path="", name=name)
    session.add(folder)
    await session.flush()
    folder.path = (parent.path if parent is not None else "/") + f"{folder.id}/"
    await session.commit()
    return _to_out(folder, 0)


async def rename_folder(session: AsyncSession, ctx: Ctx, folder_id: int, name: str) -> FolderOut:
    folder = await _get_folder(session, ctx, folder_id, access.EDIT)
    await _ensure_name_free(session, folder.kb_id, folder.parent_id, name, exclude_id=folder.id)
    # 物化路径不随重命名变化。
    folder.name = name
    await session.commit()
    return _to_out(folder, await _direct_doc_count(session, folder.id))


async def delete_folder(session: AsyncSession, ctx: Ctx, folder_id: int) -> None:
    """删除空文件夹；有下级文件夹或文档时拒绝，不自动搬走文档、不级联删除。"""
    folder = await _get_folder(session, ctx, folder_id, access.EDIT)
    child = await session.scalar(
        sa.select(KbFolder.id).where(KbFolder.parent_id == folder.id).limit(1)
    )
    if child is not None:
        raise FolderHasChildrenError()
    document = await session.scalar(
        sa.select(Document.id).where(Document.folder_id == folder.id).limit(1)
    )
    if document is not None:
        raise FolderNotEmptyError()
    await session.delete(folder)
    await session.commit()
