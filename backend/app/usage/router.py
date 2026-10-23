"""用量统计接口。实现留空，P2-08 填充。全部要求 `usage.view`。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.schemas import Pagination
from app.iam.deps import require_perm
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
    raise NotImplementedError


@router.get("/usage/daily", response_model=UsageDailyOut, summary="用量按天趋势")
async def usage_daily(
    from_: Annotated[datetime, Query(alias="from")],
    to: datetime,
    tz: str,
    model_id: int | None = None,
    org_id: int | None = None,
    app_id: int | None = None,
    source: UsageSource | None = None,
) -> UsageDailyOut:
    raise NotImplementedError


@router.get("/usage/meta", response_model=UsageMetaOut, summary="用量来源列表")
async def usage_meta() -> UsageMetaOut:
    raise NotImplementedError
