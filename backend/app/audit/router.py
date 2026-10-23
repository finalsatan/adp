"""审计日志查询接口。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actions import action_label, list_actions, list_target_types, target_type_label
from app.audit.schemas import AuditMetaOut, AuditOut, OptionOut
from app.audit.tables import AuditLog
from app.core.db import get_session
from app.core.schemas import AuditResult, Page, Pagination
from app.iam.deps import CurrentCtx, require_perm
from app.iam.scope import scope_org_ids_sql
from app.iam.tables import Org

router = APIRouter(
    prefix="/api/v1",
    tags=["审计"],
    dependencies=[Depends(require_perm("audit.view"))],
)


def _to_out(row: AuditLog) -> AuditOut:
    return AuditOut(
        id=row.id,
        at=row.at,
        actor_id=row.actor_id,
        actor_name=row.actor_name,
        actor_org_name=row.actor_org_name,
        action=row.action,
        action_label=action_label(row.action),
        target_type=row.target_type,
        target_type_label=target_type_label(row.target_type),
        target_id=row.target_id,
        target_desc=row.target_desc,
        target_org_name=row.target_org_name,
        result=row.result,
        ip=row.ip,
        detail=row.detail,
    )


@router.get("/audit-logs", response_model=Page[AuditOut], summary="审计日志列表")
async def list_audit_logs(
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    pagination: Annotated[Pagination, Depends()],
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target: str | None = None,
    org_id: int | None = None,
    result: AuditResult | None = None,
) -> Page[AuditOut]:
    conditions = []
    if from_ is not None:
        conditions.append(AuditLog.at >= from_)
    if to is not None:
        conditions.append(AuditLog.at <= to)
    if actor:
        conditions.append(AuditLog.actor_name.ilike(f"%{actor}%"))
    if action:
        conditions.append(AuditLog.action == action)
    if target_type:
        conditions.append(AuditLog.target_type == target_type)
    if target:
        conditions.append(AuditLog.target_desc.ilike(f"%{target}%"))
    if result is not None:
        conditions.append(AuditLog.result == result.value)

    # 管理范围：系统管理员不加条件；组织管理员看操作人组织或对象组织在范围内的记录。
    scope = scope_org_ids_sql(ctx)
    if scope is not None:
        conditions.append(or_(AuditLog.actor_org_id.in_(scope), AuditLog.target_org_id.in_(scope)))

    if org_id is not None:
        org_path = await session.scalar(select(Org.path).where(Org.id == org_id))
        if org_path is None:
            return Page[AuditOut](items=[], total=0)
        subtree = select(Org.id).where(Org.path.like(f"{org_path}%"))
        if scope is not None:
            subtree = subtree.where(Org.id.in_(scope))
        conditions.append(
            or_(AuditLog.actor_org_id.in_(subtree), AuditLog.target_org_id.in_(subtree))
        )

    total = await session.scalar(select(func.count()).select_from(AuditLog).where(*conditions))
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.at.desc(), AuditLog.id.desc())
            .offset((pagination.page - 1) * pagination.page_size)
            .limit(pagination.page_size)
        )
    ).all()
    return Page(items=[_to_out(row) for row in rows], total=total or 0)


@router.get("/audit-logs/meta", response_model=AuditMetaOut, summary="审计动作与对象类型")
async def get_audit_meta() -> AuditMetaOut:
    return AuditMetaOut(
        actions=[OptionOut(code=code, label=label) for code, label in list_actions()],
        target_types=[OptionOut(code=code, label=label) for code, label in list_target_types()],
    )
