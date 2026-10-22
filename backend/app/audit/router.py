"""审计日志接口。实现留空，P1-07 填充。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.audit.schemas import AuditMetaOut, AuditOut
from app.core.schemas import AuditResult, Page, Pagination
from app.iam.deps import require_perm

router = APIRouter(
    prefix="/api/v1",
    tags=["审计"],
    dependencies=[Depends(require_perm("audit.view"))],
)


@router.get("/audit-logs", response_model=Page[AuditOut], summary="审计日志列表")
async def list_audit_logs(
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
    raise NotImplementedError


@router.get("/audit-logs/meta", response_model=AuditMetaOut, summary="审计动作与对象类型")
async def get_audit_meta() -> AuditMetaOut:
    raise NotImplementedError
