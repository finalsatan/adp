"""审计日志接口的返回结构。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.schemas import AuditResult


class AuditOut(BaseModel):
    id: int
    at: datetime
    actor_id: int | None
    actor_name: str | None
    actor_org_name: str | None
    action: str
    action_label: str
    target_type: str | None
    target_type_label: str | None
    target_id: int | None
    target_desc: str | None
    target_org_name: str | None
    result: AuditResult
    ip: str | None
    detail: dict[str, Any] | None


class OptionOut(BaseModel):
    code: str
    label: str


class AuditMetaOut(BaseModel):
    actions: list[OptionOut]
    target_types: list[OptionOut]
