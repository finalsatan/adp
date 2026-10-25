"""知识库（M2）模块：接口契约（P3-01），实现在 P3-04 起的任务卡。

启动时由 `app/main.py` 调用 `register(storage)` 注册资源类型 `kb`。删除知识库时
删索引表、删 `knowledge_bases` 行（外键级联）并清对象存储前缀（知识库技术方案 11.1）。
"""

import logging
from typing import TYPE_CHECKING

from app.core.storage import Storage, kb_prefix
from app.kb import index_table
from app.kb.tables import KnowledgeBase
from app.resources.registry import ResourceType, register_resource_type

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.resources.tables import Resource

logger = logging.getLogger(__name__)

# 应用启动时注入的 Storage 实例；on_delete 钩子没有请求上下文，只能从这里取。
_storage: Storage | None = None


def configure(storage: Storage) -> None:
    """注入对象存储实例（`app/main.py` 调用）。"""
    global _storage
    _storage = storage


async def _on_delete(session: "AsyncSession", resource: "Resource") -> None:
    """删除知识库的类型专属处理：删索引表、删业务行、清对象存储前缀。"""
    await index_table.drop_index_table(session, resource.id)
    kb = await session.get(KnowledgeBase, resource.id)
    if kb is not None:
        await session.delete(kb)
    await session.flush()
    # 前缀删除失败只记 ERROR，不让接口 500；下次维护任务再清（P3-04 补充）。
    if _storage is not None:
        try:
            await _storage.delete_prefix(kb_prefix(resource.id))
        except Exception:
            logger.error("删除知识库对象存储前缀失败: kb_id=%s", resource.id, exc_info=True)


def register(storage: Storage) -> None:
    """注册资源类型 `kb` 与 `scheme`；重复调用只覆盖注册表项。

    内置方案不登记资源行（知识库技术方案 14），`scheme` 类型第五期做自定义方案时用；
    可见范围固定为全平台。
    """
    configure(storage)
    register_resource_type(
        ResourceType(code="kb", label="知识库", fixed_visibility=None, on_delete=_on_delete)
    )
    register_resource_type(
        ResourceType(code="scheme", label="入库方案", fixed_visibility="platform")
    )
