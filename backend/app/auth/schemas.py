"""认证接口的请求和返回结构。"""

from pydantic import BaseModel

from app.core.schemas import RequestModel


class LoginIn(RequestModel):
    username: str
    password: str


class ChangePasswordIn(RequestModel):
    old_password: str | None = None
    new_password: str


class MustChangePasswordOut(BaseModel):
    must_change_password: bool


class AuthPolicyOut(BaseModel):
    password_min_length: int
    password_min_classes: int
    access_token_minutes: int
