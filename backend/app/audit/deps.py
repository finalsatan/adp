"""审计依赖：标记待审计的动作，并在成功或失败时写一条记录。

用法（路由的 dependencies 列表里 audited 放第一个，先于 require_perm 执行）：
    dependencies=[Depends(audited("user.create")), Depends(require_perm("user.manage"))]
路由成功、业务提交后调用 `await audit_success(request, target, detail)`；
失败由全局 AppError 处理器调用 `audit_failure`，权限不足的 403 也能记下来。
"""

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.audit.service import AuditTarget, record
from app.core.schemas import AuditResult


@dataclass
class AuditState:
    action: str
    target: AuditTarget | None = None
    recorded: bool = False


def audited(action: str):
    """返回标记 request.state.audit 的依赖。"""

    async def _dependency(request: Request) -> None:
        request.state.audit = AuditState(action=action)

    return _dependency


def set_audit_target(request: Request, target: AuditTarget | None) -> None:
    """路由可以提前设置对象，失败时处理器也能记下对象。"""
    state: AuditState | None = getattr(request.state, "audit", None)
    if state is not None:
        state.target = target


async def audit_success(
    request: Request,
    target: AuditTarget | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """业务提交成功后写 success。"""
    state: AuditState | None = getattr(request.state, "audit", None)
    if state is None or state.recorded:
        return
    if target is not None:
        state.target = target
    ctx = getattr(request.state, "ctx", None)
    await record(ctx, state.action, state.target, AuditResult.success, detail)
    state.recorded = True


async def audit_failure(request: Request, code: str) -> None:
    """AppError 处理器调用：已登录且尚未记录时写一条 failure。"""
    state: AuditState | None = getattr(request.state, "audit", None)
    if state is None or state.recorded:
        return
    ctx = getattr(request.state, "ctx", None)
    if ctx is None:
        return
    await record(ctx, state.action, state.target, AuditResult.failure, {"code": code})
    state.recorded = True
