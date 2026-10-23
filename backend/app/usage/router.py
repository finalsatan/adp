"""用量统计接口：汇总、按天趋势和来源列表。业务逻辑在 `service`。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.schemas import Pagination
from app.iam.deps import CurrentCtx, require_perm
from app.usage import service
from app.usage.schemas import (
    SortOrder,
    UsageDailyOut,
    UsageGroupBy,
    UsageMetaOut,
    UsageSortBy,
    UsageSource,
    UsageSummaryOut,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["用量"],
    dependencies=[Depends(require_perm("usage.view"))],
)


@router.get("/usage/summary", response_model=UsageSummaryOut, summary="用量汇总")
async def usage_summary(
    pagination: Annotated[Pagination, Depends()],
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[datetime, Query(alias="from")],
    to: datetime,
    group_by: UsageGroupBy,
    model_id: int | None = None,
    org_id: int | None = None,
    app_id: int | None = None,
    source: UsageSource | None = None,
    sort_by: UsageSortBy = UsageSortBy.calls,
    order: SortOrder = SortOrder.desc,
) -> UsageSummaryOut:
    return await service.summary(
        session,
        ctx,
        pagination,
        from_,
        to,
        group_by,
        model_id,
        org_id,
        app_id,
        source,
        sort_by,
        order,
    )


@router.get("/usage/daily", response_model=UsageDailyOut, summary="用量按天趋势")
async def usage_daily(
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[datetime, Query(alias="from")],
    to: datetime,
    tz: str,
    model_id: int | None = None,
    org_id: int | None = None,
    app_id: int | None = None,
    source: UsageSource | None = None,
) -> UsageDailyOut:
    return await service.daily(session, ctx, from_, to, tz, model_id, org_id, app_id, source)


@router.get("/usage/meta", response_model=UsageMetaOut, summary="用量来源列表")
async def usage_meta() -> UsageMetaOut:
    return service.meta()
