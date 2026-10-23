"""资源类型注册表与引用检查扩展点（技术方案 6.2、7.3）。

第一期不注册任何类型，接口和检查逻辑照常上线；第三期起知识库、入库方案、
应用等模块在启动时调用 `register_resource_type` 注册自己。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.resources.tables import Resource

# 类型专属钩子：删除前、启停时调用；为空时只做通用处理。
OnDelete = Callable[["AsyncSession", "Resource"], Awaitable[None]]
OnStatusChange = Callable[["AsyncSession", "Resource"], Awaitable[None]]


@dataclass(frozen=True)
class ResourceType:
    """一种可登记进资源总览的资源类型。"""

    code: str
    label: str
    fixed_visibility: str | None = None
    on_delete: OnDelete | None = None
    on_status_change: OnStatusChange | None = None


@dataclass(frozen=True)
class RefBlocker:
    """引用检查返回的“会因此不能再引用它”的引用方。"""

    id: int
    type: str
    name: str


# 引用检查函数：返回缩小可见范围 / 移除协作者后会失去引用的引用方。
RefChecker = Callable[
    ["AsyncSession", "Resource", "str | None", "int | None"],
    Awaitable[list[RefBlocker]],
]

_types: dict[str, ResourceType] = {}
_ref_checkers: list[RefChecker] = []


def register_resource_type(resource_type: ResourceType) -> None:
    """注册资源类型；同 code 重复注册会覆盖。"""
    _types[resource_type.code] = resource_type


def unregister_resource_type(code: str) -> None:
    """注销资源类型（测试收尾用）。"""
    _types.pop(code, None)


def get_resource_type(code: str) -> ResourceType | None:
    return _types.get(code)


def list_resource_types() -> list[ResourceType]:
    """按注册顺序返回。"""
    return list(_types.values())


def register_ref_checker(fn: RefChecker) -> None:
    """注册引用检查函数，重复注册同一函数会忽略。"""
    if fn not in _ref_checkers:
        _ref_checkers.append(fn)


def unregister_ref_checker(fn: RefChecker) -> None:
    """注销引用检查函数（测试收尾用）。"""
    if fn in _ref_checkers:
        _ref_checkers.remove(fn)


def list_ref_checkers() -> list[RefChecker]:
    return list(_ref_checkers)
