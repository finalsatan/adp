"""公共枚举、分页结构和请求基类。"""

from enum import StrEnum
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict


class Role(StrEnum):
    """用户角色。"""

    sys_admin = "sys_admin"
    org_admin = "org_admin"
    builder = "builder"
    end_user = "end_user"


class UserStatus(StrEnum):
    """用户状态。"""

    active = "active"
    disabled = "disabled"


class Visibility(StrEnum):
    """资源可见范围。"""

    private = "private"
    org = "org"
    platform = "platform"


class ResourceStatus(StrEnum):
    """资源状态。"""

    enabled = "enabled"
    disabled = "disabled"


class AuditResult(StrEnum):
    """审计结果。"""

    success = "success"
    failure = "failure"


class RequestModel(BaseModel):
    """所有请求结构的基类：不接受契约以外的字段。"""

    model_config = ConfigDict(extra="forbid")


class Page[T](BaseModel):
    """分页返回。"""

    items: list[T]
    total: int


class Pagination:
    """分页查询参数依赖：page 从 1 开始，page_size 默认 10、最大 100。"""

    def __init__(
        self,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    ) -> None:
        self.page = page
        self.page_size = page_size


class UserBrief(BaseModel):
    """用户简要信息。"""

    id: int
    username: str
    name: str


class OrgBrief(BaseModel):
    """组织简要信息。"""

    id: int
    name: str
