"""应用模块包。资源类型注册（P4-03）与表定义（P4-02）。

启动时由 `app/main.py` 调用 `register()` 注册资源类型 `app`。删除应用是软删除资源，
`on_delete` 只清该应用登记的 `resource_refs`（它引用了哪些模型 / 知识库），不删
`apps`、`app_versions`、`app_usage_targets`、会话、日志、反馈（应用技术方案 12.2）。
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa

from app.resources.registry import ResourceType, register_ref_checker, register_resource_type

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.resources.tables import Resource

APP_TYPE = "app"


async def _on_delete(session: "AsyncSession", resource: "Resource") -> None:
    """删除应用的类型专属处理：只清引用登记，业务行保留（软删除）。"""
    from app.resources.tables import ResourceRef

    await session.execute(sa.delete(ResourceRef).where(ResourceRef.ref_resource_id == resource.id))
    await session.flush()


def register() -> None:
    """注册资源类型 `app`（可见范围由资源表决定，不固定）与知识库引用检查。"""
    from app.apps import refs

    register_resource_type(
        ResourceType(code=APP_TYPE, label="应用", fixed_visibility=None, on_delete=_on_delete)
    )
    # 知识库缩小可见范围 / 移除协作者前，检查引用它的应用是否还能引用（应用技术方案 5.4）。
    register_ref_checker(refs._kb_ref_checker)
