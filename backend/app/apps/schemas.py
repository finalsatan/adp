"""应用接口的枚举、请求与返回结构（P4-01 契约）。

实现见 P4-03（应用管理、使用范围）、P4-04（草稿与版本）。
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from app.apps.config import AppConfig, AppType
from app.core.schemas import OrgBrief, RequestModel, ResourceStatus, UserBrief, Visibility
from app.kb.schemas import KbKind

__all__ = [
    "AppConfig",
    "AppType",
    "AppStatus",
    "UsageScopeType",
    "UsageTargetType",
    "AppVersionBrief",
    "UsageScopeOut",
    "UsageScopeIn",
    "AppOut",
    "AppCreate",
    "AppUpdate",
    "MyAppOut",
    "DraftOut",
    "DraftIn",
    "PromptPreviewOut",
    "KbModelBrief",
    "KbCandidateOut",
    "PublishIn",
    "PublishProblemOut",
    "AppVersionOut",
]


class AppStatus(StrEnum):
    """应用发布状态，由 `published_version_id` 是否为空和资源启停状态推导（技术方案 10）。"""

    unpublished = "unpublished"
    published = "published"
    disabled = "disabled"


class UsageScopeType(StrEnum):
    """使用范围三档（需求 5.4.3）。"""

    platform = "platform"
    orgs = "orgs"
    users = "users"


class UsageTargetType(StrEnum):
    """使用范围名单的目标类型（`app_usage_targets.target_type`）。"""

    org = "org"
    user = "user"


class AppVersionBrief(BaseModel):
    """当前发布版本的简要信息。"""

    id: int
    version_no: int
    published_at: datetime


class UsageScopeOut(BaseModel):
    """使用范围。`platform` 时两个列表为空。"""

    scope: UsageScopeType
    orgs: list[OrgBrief]
    users: list[UserBrief]


class UsageScopeIn(RequestModel):
    """全量替换使用范围。"""

    scope: UsageScopeType
    org_ids: list[int] = Field(default_factory=list)
    user_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_scope(self) -> "UsageScopeIn":
        if self.scope == UsageScopeType.platform:
            if self.org_ids or self.user_ids:
                raise ValueError("全平台时不能指定组织或用户")
        elif self.scope == UsageScopeType.orgs:
            if not self.org_ids:
                raise ValueError("指定组织时至少选择一个组织")
        elif self.scope == UsageScopeType.users and not self.user_ids:
            raise ValueError("指定用户时至少选择一个用户")
        return self


class AppOut(BaseModel):
    """应用详情。`chat_path` 供前端拼对话页链接。"""

    id: int
    name: str
    description: str | None
    app_type: AppType
    org: OrgBrief
    owner: UserBrief
    visibility: Visibility
    status: AppStatus
    published_version: AppVersionBrief | None
    draft_has_changes: bool
    draft_updated_at: datetime | None
    usage_scope: UsageScopeOut
    chat_path: str
    created_at: datetime
    updated_at: datetime
    editable: bool
    manageable: bool


class AppCreate(RequestModel):
    """新建应用。系统管理员必须指定 `org_id`。"""

    app_type: AppType
    name: str = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    visibility: Visibility
    org_id: int | None = None


class AppUpdate(RequestModel):
    """编辑应用。带 `app_type` 返回 422 `APP_TYPE_IMMUTABLE`（实现见 P4-03）。"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    app_type: AppType | None = None


class MyAppOut(BaseModel):
    """终端用户“我的应用”列表项。"""

    id: int
    name: str
    description: str | None
    app_type: AppType
    org: OrgBrief
    chat_path: str


class DraftOut(BaseModel):
    """草稿。`has_changes` 为草稿哈希与当前发布版本哈希不同。"""

    draft_revision: int
    config: AppConfig
    has_changes: bool
    updated_by: UserBrief | None
    updated_at: datetime | None


class DraftIn(RequestModel):
    """保存草稿。`draft_revision` 不一致时返回 409 `DRAFT_CONFLICT`。"""

    draft_revision: int
    config: AppConfig


class PromptPreviewOut(BaseModel):
    """系统拼接的固定部分预览（应用技术方案 7.1）。"""

    fixed_part: str
    default_system_prompt: str
    prompt_version: str


class KbModelBrief(BaseModel):
    """候选知识库的向量化模型。"""

    id: int
    name: str


class KbCandidateOut(BaseModel):
    """可添加的知识库（应用技术方案 5.4，原型 `#d-addkb`）。"""

    kb_id: int
    name: str
    kind: KbKind
    embedding_model: KbModelBrief
    org: OrgBrief
    visibility: Visibility
    status: ResourceStatus
    added: bool
    selectable: bool
    reason: str | None


class PublishIn(RequestModel):
    """发布。"""

    note: str = Field(min_length=1, max_length=200)


class PublishProblemOut(BaseModel):
    """发布校验未通过时的一条问题。"""

    code: str
    message: str
    field: str | None = None


class AppVersionOut(BaseModel):
    """一个发布版本。`is_current` 为是否为当前发布版本。"""

    id: int
    version_no: int
    note: str
    config: AppConfig
    config_hash: str
    published_by: UserBrief
    published_at: datetime
    is_current: bool
