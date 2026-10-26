"""个人、组织、用户接口的请求和返回结构。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.schemas import OrgBrief, RequestModel, Role, UserBrief, UserStatus

# 创建、编辑用户时可选的角色（不能直接创建系统管理员）
AssignableRole = Literal[Role.org_admin, Role.builder, Role.end_user]

USERNAME_PATTERN = r"^[A-Za-z0-9_.-]+$"


class MeOut(BaseModel):
    id: int
    username: str
    name: str
    email: str | None
    phone: str | None
    role: Role
    org: OrgBrief | None
    permissions: list[str]
    must_change_password: bool


class MePatch(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    email: str | None = None
    phone: str | None = None


class OrgOut(BaseModel):
    id: int
    parent_id: int | None
    path: str
    name: str
    remark: str | None
    admins: list[UserBrief]
    user_count: int
    resource_count: int
    child_count: int
    created_at: datetime
    editable: bool


class OrgTreeOut(BaseModel):
    """只读组织树的一项，仅结构信息（无成员、管理员等）。"""

    id: int
    parent_id: int | None
    name: str


class OrgCreate(RequestModel):
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=50)
    remark: str | None = Field(default=None, max_length=200)


class OrgPatch(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    remark: str | None = Field(default=None, max_length=200)


class OrgMemberOut(BaseModel):
    id: int
    username: str
    name: str
    role: Role
    status: UserStatus
    manageable: bool


class UserOut(BaseModel):
    id: int
    username: str
    name: str
    email: str | None
    phone: str | None
    remark: str | None
    org: OrgBrief | None
    role: Role
    status: UserStatus
    locked: bool
    last_login_at: datetime | None
    created_at: datetime
    created_by: UserBrief | None
    owned_resource_count: int
    manageable: bool


class UserCreate(RequestModel):
    username: str = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    password: str
    name: str = Field(min_length=1, max_length=50)
    email: str | None = None
    phone: str | None = None
    remark: str | None = None
    org_id: int
    role: AssignableRole


class UserPatch(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    email: str | None = None
    phone: str | None = None
    remark: str | None = None
    org_id: int | None = None
    role: AssignableRole | None = None


class ResetPasswordIn(RequestModel):
    new_password: str


class RevokeSysAdminIn(RequestModel):
    org_id: int
    role: AssignableRole


class UserSearchOut(BaseModel):
    id: int
    username: str
    name: str
    org: OrgBrief | None
