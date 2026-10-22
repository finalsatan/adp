"""资源总览与协作者接口的请求和返回结构。"""

from datetime import datetime

from pydantic import BaseModel

from app.core.schemas import (
    OrgBrief,
    RequestModel,
    ResourceStatus,
    UserBrief,
    Visibility,
)
from app.iam.schemas import UserSearchOut


class ResourceTypeOut(BaseModel):
    code: str
    label: str
    fixed_visibility: Visibility | None


class ResourceOut(BaseModel):
    id: int
    type: str
    type_label: str
    name: str
    description: str | None
    org: OrgBrief
    owner: UserBrief
    visibility: Visibility
    status: ResourceStatus
    created_at: datetime
    updated_at: datetime


class MemberOut(BaseModel):
    user: UserSearchOut
    added_by: UserBrief
    added_at: datetime


class VisibilityIn(RequestModel):
    visibility: Visibility


class MemberIn(RequestModel):
    user_id: int
