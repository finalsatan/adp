"""系统设置接口的请求和返回结构。"""

from pydantic import BaseModel, Field

from app.core.schemas import RequestModel


class PasswordSettings(BaseModel):
    min_length: int = Field(default=8, ge=8, le=64)
    min_classes: int = Field(default=3, ge=1, le=4)


class LoginSettings(BaseModel):
    lock_threshold: int = Field(default=5, ge=3, le=20)
    lock_minutes: int = Field(default=15, ge=1, le=1440)


class SessionSettings(BaseModel):
    access_minutes: int = Field(default=15, ge=5, le=60)
    refresh_days: int = Field(default=7, ge=1, le=30)


class ModelCallSettings(BaseModel):
    ingest_rate_ratio: float = Field(default=0.5, ge=0.1, le=0.9)
    model_host_allowlist: list[str] = Field(default_factory=list, max_length=100)


class SettingsOut(BaseModel):
    password: PasswordSettings
    login: LoginSettings
    session: SessionSettings
    model_call: ModelCallSettings


class PasswordSettingsPatch(RequestModel):
    min_length: int | None = Field(default=None, ge=8, le=64)
    min_classes: int | None = Field(default=None, ge=1, le=4)


class LoginSettingsPatch(RequestModel):
    lock_threshold: int | None = Field(default=None, ge=3, le=20)
    lock_minutes: int | None = Field(default=None, ge=1, le=1440)


class SessionSettingsPatch(RequestModel):
    access_minutes: int | None = Field(default=None, ge=5, le=60)
    refresh_days: int | None = Field(default=None, ge=1, le=30)


class ModelCallSettingsPatch(RequestModel):
    ingest_rate_ratio: float | None = Field(default=None, ge=0.1, le=0.9)
    model_host_allowlist: list[str] | None = Field(default=None, max_length=100)


class SettingsPatch(RequestModel):
    password: PasswordSettingsPatch | None = None
    login: LoginSettingsPatch | None = None
    session: SessionSettingsPatch | None = None
    model_call: ModelCallSettingsPatch | None = None
