"""审计日志写入。

业务事务提交成功后，用独立的数据库会话写一条审计记录（技术方案 8.2、D7）。
写入失败记 ERROR 日志并立即重试一次，仍失败不影响业务返回。
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.tables import AuditLog
from app.core.context import request_id_var
from app.core.schemas import AuditResult
from app.iam.tables import Org

if TYPE_CHECKING:
    from app.iam.context import Ctx

logger = logging.getLogger(__name__)

_sessionmaker: async_sessionmaker[AsyncSession] | None = None

# detail 里键名含这些片段的项一律删除，防止误记敏感信息。
_SENSITIVE_KEY_PARTS = ("password", "token", "secret", "api_key")


@dataclass(frozen=True)
class AuditTarget:
    """审计对象。desc 是给人看的对象描述，如用户名、组织名、资源名。"""

    type: str
    id: int | None = None
    desc: str | None = None
    org_id: int | None = None


@dataclass(frozen=True)
class AuditActor:
    """没有请求上下文时的操作人（登录失败时由 P1-08 传入）。"""

    id: int | None
    name: str
    org_id: int | None = None
    org_name: str | None = None
    ip: str | None = None


def configure(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """由 create_app 注入应用共用的会话工厂。"""
    global _sessionmaker
    _sessionmaker = sessionmaker


def _strip_sensitive(value: Any) -> Any:
    """递归删除 detail 中键名含敏感片段的项。"""
    if isinstance(value, dict):
        return {
            key: _strip_sensitive(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_strip_sensitive(item) for item in value]
    return value


def _build_fields(
    ctx: "Ctx | None",
    action: str,
    target: AuditTarget | None,
    result: AuditResult,
    detail: dict[str, Any] | None,
    actor: AuditActor | None,
) -> dict[str, Any]:
    if ctx is not None:
        actor_id = ctx.user_id
        actor_name = ctx.username
        actor_org_id = ctx.org_id
        actor_org_name = ctx.org_name
        ip = ctx.ip
        request_id = ctx.request_id or request_id_var.get()
    elif actor is not None:
        actor_id = actor.id
        actor_name = actor.name
        actor_org_id = actor.org_id
        actor_org_name = actor.org_name
        ip = actor.ip
        request_id = request_id_var.get()
    else:
        actor_id = None
        actor_name = ""
        actor_org_id = None
        actor_org_name = None
        ip = None
        request_id = request_id_var.get()

    return {
        "actor_id": actor_id,
        "actor_name": actor_name,
        "actor_org_id": actor_org_id,
        "actor_org_name": actor_org_name,
        "action": action,
        "target_type": target.type if target is not None else None,
        "target_id": target.id if target is not None else None,
        "target_desc": target.desc if target is not None else None,
        "target_org_id": target.org_id if target is not None else None,
        "target_org_name": None,
        "result": result.value,
        "ip": ip,
        "request_id": request_id,
        "detail": _strip_sensitive(detail),
    }


async def _org_name(session: AsyncSession, org_id: int) -> str | None:
    """按 id 查组织名，含已软删除的组织。"""
    return await session.scalar(sa.select(Org.name).where(Org.id == org_id))


async def _insert(fields: dict[str, Any]) -> None:
    """独立的会话写入并提交；对象组织名在这里按 id 补齐。"""
    if _sessionmaker is None:  # pragma: no cover - configure 在 create_app 里调用
        raise RuntimeError("audit sessionmaker 未配置")
    async with _sessionmaker() as session:
        if fields["actor_org_id"] is not None and fields["actor_org_name"] is None:
            fields["actor_org_name"] = await _org_name(session, fields["actor_org_id"])
        if fields["target_org_id"] is not None and fields["target_org_name"] is None:
            fields["target_org_name"] = await _org_name(session, fields["target_org_id"])
        session.add(AuditLog(**fields))
        await session.commit()


async def record(
    ctx: "Ctx | None",
    action: str,
    target: AuditTarget | None,
    result: AuditResult,
    detail: dict[str, Any] | None = None,
    actor: AuditActor | None = None,
) -> None:
    """写一条审计。失败不影响业务返回，只写 ERROR 日志并重试一次。"""
    if _sessionmaker is None:  # pragma: no cover - configure 在 create_app 里调用
        logger.error("审计会话未配置，记录丢失: action=%s", action)
        return

    fields = _build_fields(ctx, action, target, result, detail, actor)
    try:
        await _insert(fields)
    except Exception:
        logger.error("审计写入失败，重试一次: action=%s", action, exc_info=True)
        try:
            await _insert(fields)
        except Exception:
            logger.error("审计写入重试仍失败，已丢弃: action=%s", action, exc_info=True)
