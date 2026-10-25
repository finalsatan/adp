"""系统设置服务：默认值、校验、读写与进程内缓存。"""

import ipaddress
import re
import time
from datetime import UTC, datetime

import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings.tables import Setting

# 设置很少修改，进程内缓存 30 秒；多副本部署时最多 30 秒不一致。
CACHE_TTL_SECONDS = 30.0

# RFC 1123 主机名：每段 1–63 字符，字母数字和连字符，不能以连字符开头或结尾。
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
)


def _is_host_or_network(value: str) -> bool:
    """主机名、IP 或 CIDR 网段。"""
    if not value:
        return False
    try:
        ipaddress.ip_network(value, strict=False)
        return True
    except ValueError:
        return bool(_HOSTNAME_RE.match(value))


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

    @field_validator("model_host_allowlist")
    @classmethod
    def _check_allowlist(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _is_host_or_network(item):
                raise ValueError(f"不是有效的主机名、IP 或 CIDR 网段：{item}")
        return value


class UploadSettings(BaseModel):
    """上传与解析（第三期，需求 10.8）。"""

    max_file_size_mb: int = Field(default=200, ge=1, le=2048)
    parse_concurrency: int = Field(default=4, ge=1, le=32)
    kb_parse_concurrency: int = Field(default=2, ge=1, le=16)


class PlatformSettings(BaseModel):
    """各组设置的汇总；以后新增分组只需在这里加一个 Pydantic 类。"""

    password: PasswordSettings = Field(default_factory=PasswordSettings)
    login: LoginSettings = Field(default_factory=LoginSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    model_call: ModelCallSettings = Field(default_factory=ModelCallSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)


_cache: tuple[float, PlatformSettings] | None = None


def clear_cache() -> None:
    """清空进程内缓存（更新设置或测试时调用）。"""
    global _cache
    _cache = None


async def get_settings(session: AsyncSession) -> PlatformSettings:
    """读取全部设置；库里没有的分组或字段用默认值。"""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]

    result = await session.execute(sa.select(Setting))
    rows = {row.key: row.value for row in result.scalars()}
    data = {name: rows[name] for name in PlatformSettings.model_fields if name in rows}
    settings = PlatformSettings.model_validate(data)
    _cache = (now, settings)
    return settings


async def update_settings(
    session: AsyncSession,
    patch: dict,
    actor_id: int,
) -> tuple[PlatformSettings, PlatformSettings]:
    """按分组合并、校验、写库，返回（修改前，修改后）。不写审计。"""
    before = await get_settings(session)
    merged = before.model_dump()
    for group, changes in patch.items():
        if group not in PlatformSettings.model_fields:
            continue
        merged[group] = {**merged[group], **(changes or {})}

    after = PlatformSettings.model_validate(merged)

    now = datetime.now(UTC)
    after_values = after.model_dump()
    for group in patch:
        if group not in PlatformSettings.model_fields:
            continue
        row = await session.get(Setting, group)
        value = after_values[group]
        if row is None:
            session.add(Setting(key=group, value=value, updated_by=actor_id, updated_at=now))
        else:
            row.value = value
            row.updated_by = actor_id
            row.updated_at = now
    await session.commit()
    clear_cache()
    return before, after
