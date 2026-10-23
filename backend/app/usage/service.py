"""用量统计业务逻辑：汇总、按天趋势和来源列表（技术方案 9.7）。

路由只做参数接收、权限依赖和调用这里的函数。
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import InvalidTimeRangeError, PermissionDeniedError, ValidationError
from app.core.schemas import Pagination, Role
from app.gateway.tables import ModelUsage
from app.iam.context import Ctx
from app.iam.scope import in_scope, scope_org_ids_sql
from app.iam.tables import Org, User
from app.models.tables import Model
from app.resources.tables import Resource
from app.usage.schemas import (
    USAGE_SOURCE_LABELS,
    SortOrder,
    UsageDailyItem,
    UsageDailyOut,
    UsageGroupBy,
    UsageItem,
    UsageMetaOut,
    UsageSortBy,
    UsageSource,
    UsageSourceOption,
    UsageSummaryOut,
    UsageTotals,
)

# 时间范围最长 366 天（技术方案 9.7）。
MAX_RANGE = timedelta(days=366)
_APP_SOURCES = ("app", "app_debug")


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _validate_range(from_: datetime, to: datetime) -> tuple[datetime, datetime]:
    """校验时间范围；不合法抛 422 INVALID_TIME_RANGE。"""
    if from_.tzinfo is None:
        from_ = from_.replace(tzinfo=UTC)
    if to.tzinfo is None:
        to = to.replace(tzinfo=UTC)
    if to <= from_ or to - from_ > MAX_RANGE:
        raise InvalidTimeRangeError()
    return from_, to


def _aggregates() -> tuple[sa.ColumnElement, ...]:
    """筛选范围内的一组聚合列：调用次数、失败次数、输入和输出 token。"""
    return (
        sa.func.count().label("calls"),
        sa.func.count().filter(sa.not_(ModelUsage.ok)).label("failures"),
        sa.func.coalesce(sa.func.sum(ModelUsage.input_tokens), 0).label("input_tokens"),
        sa.func.coalesce(sa.func.sum(ModelUsage.output_tokens), 0).label("output_tokens"),
    )


async def _conditions(
    session: AsyncSession,
    ctx: Ctx,
    from_: datetime,
    to: datetime,
    model_id: int | None,
    org_id: int | None,
    app_id: int | None,
    source: UsageSource | None,
) -> list[sa.ColumnElement]:
    """公共筛选条件，权限范围直接拼进 WHERE。"""
    conds: list[sa.ColumnElement] = [ModelUsage.at >= from_, ModelUsage.at < to]

    scope = scope_org_ids_sql(ctx)
    if scope is not None:
        conds.append(ModelUsage.org_id.in_(scope))

    if model_id is not None:
        conds.append(ModelUsage.model_id == model_id)
    if source is not None:
        conds.append(ModelUsage.source == source.value)
    if app_id is not None:
        conds.append(ModelUsage.source.in_(_APP_SOURCES))
        conds.append(ModelUsage.source_id == app_id)
    if org_id is not None:
        conds.append(await _org_condition(session, ctx, org_id))
    return conds


async def _org_condition(session: AsyncSession, ctx: Ctx, org_id: int) -> sa.ColumnElement:
    """组织筛选：含下级的全部 id（含已删除组织）；越权返回 403。"""
    org = await session.get(Org, org_id)
    if org is None:
        return sa.false()
    if ctx.role == Role.org_admin and not in_scope(ctx, org.path):
        raise PermissionDeniedError()
    pattern = f"{_escape_like(org.path)}%"
    return ModelUsage.org_id.in_(sa.select(Org.id).where(Org.path.like(pattern, escape="\\")))


def _summary_query(
    group_by: UsageGroupBy, conds: list[sa.ColumnElement]
) -> tuple[sa.ColumnElement, sa.Select, object, dict[UsageSortBy, sa.ColumnElement]]:
    """按维度拼分组查询，返回分组列、查询、标签格式化函数和排序用的聚合列。"""
    calls, failures, input_tokens, output_tokens = _aggregates()
    aggs = {
        UsageSortBy.calls: calls,
        UsageSortBy.failures: failures,
        UsageSortBy.input_tokens: input_tokens,
        UsageSortBy.output_tokens: output_tokens,
    }

    if group_by is UsageGroupBy.model:
        group_col = ModelUsage.model_id
        query = (
            sa.select(group_col.label("key"), Model.name.label("name"), *aggs.values())
            .select_from(ModelUsage)
            .outerjoin(Model, Model.id == ModelUsage.model_id)
            .where(*conds)
            .group_by(group_col, Model.name)
        )

        def fmt(row: sa.RowMapping) -> tuple[str, str | None]:
            return (row["name"] or "未保存的模型", None)

    elif group_by is UsageGroupBy.org:
        group_col = ModelUsage.org_id
        parent = aliased(Org)
        query = (
            sa.select(
                group_col.label("key"),
                Org.name.label("name"),
                parent.name.label("parent_name"),
                *aggs.values(),
            )
            .select_from(ModelUsage)
            .outerjoin(Org, Org.id == ModelUsage.org_id)
            .outerjoin(parent, parent.id == Org.parent_id)
            .where(*conds)
            .group_by(group_col, Org.name, parent.name)
        )

        def fmt(row: sa.RowMapping) -> tuple[str, str | None]:
            parent_name = row["parent_name"]
            return (row["name"] or "系统管理员", f"{parent_name}下级" if parent_name else None)

    elif group_by is UsageGroupBy.app:
        group_col = ModelUsage.source_id
        query = (
            sa.select(group_col.label("key"), Resource.name.label("name"), *aggs.values())
            .select_from(ModelUsage)
            .outerjoin(Resource, Resource.id == ModelUsage.source_id)
            .where(*conds, ModelUsage.source.in_(_APP_SOURCES))
            .group_by(group_col, Resource.name)
        )

        def fmt(row: sa.RowMapping) -> tuple[str, str | None]:
            return (row["name"] or "未知应用", None)

    elif group_by is UsageGroupBy.user:
        group_col = ModelUsage.user_id
        query = (
            sa.select(
                group_col.label("key"),
                User.username.label("username"),
                User.name.label("name"),
                *aggs.values(),
            )
            .select_from(ModelUsage)
            .outerjoin(User, User.id == ModelUsage.user_id)
            .where(*conds)
            .group_by(group_col, User.username, User.name)
        )

        def fmt(row: sa.RowMapping) -> tuple[str, str | None]:
            if row["username"] is None:
                return ("未知用户", None)
            return (f"{row['username']} {row['name']}", None)

    else:  # source
        group_col = ModelUsage.source
        query = (
            sa.select(group_col.label("key"), *aggs.values())
            .select_from(ModelUsage)
            .where(*conds)
            .group_by(group_col)
        )

        def fmt(row: sa.RowMapping) -> tuple[str, str | None]:
            return (USAGE_SOURCE_LABELS.get(row["key"], row["key"]), None)

    return group_col, query, fmt, aggs


async def summary(
    session: AsyncSession,
    ctx: Ctx,
    pagination: Pagination,
    from_: datetime,
    to: datetime,
    group_by: UsageGroupBy,
    model_id: int | None,
    org_id: int | None,
    app_id: int | None,
    source: UsageSource | None,
    sort_by: UsageSortBy,
    order: SortOrder,
) -> UsageSummaryOut:
    from_, to = _validate_range(from_, to)
    conds = await _conditions(session, ctx, from_, to, model_id, org_id, app_id, source)

    totals_row = (
        (await session.execute(sa.select(*_aggregates()).select_from(ModelUsage).where(*conds)))
        .mappings()
        .one()
    )
    totals = UsageTotals(
        calls=int(totals_row["calls"]),
        failures=int(totals_row["failures"]),
        input_tokens=int(totals_row["input_tokens"]),
        output_tokens=int(totals_row["output_tokens"]),
    )

    group_col, query, fmt, aggs = _summary_query(group_by, conds)
    total = await session.scalar(sa.select(sa.func.count()).select_from(query.subquery())) or 0

    sort_col = aggs[sort_by]
    direction = sort_col.desc() if order is SortOrder.desc else sort_col.asc()
    rows = (
        (
            await session.execute(
                query.order_by(direction, group_col.asc())
                .limit(pagination.page_size)
                .offset((pagination.page - 1) * pagination.page_size)
            )
        )
        .mappings()
        .all()
    )

    items: list[UsageItem] = []
    for row in rows:
        label, sub_label = fmt(row)
        items.append(
            UsageItem(
                key=str(row["key"]) if row["key"] is not None else None,
                label=label,
                sub_label=sub_label,
                calls=int(row["calls"]),
                failures=int(row["failures"]),
                input_tokens=int(row["input_tokens"]),
                output_tokens=int(row["output_tokens"]),
            )
        )
    return UsageSummaryOut(totals=totals, items=items, total=int(total))


async def daily(
    session: AsyncSession,
    ctx: Ctx,
    from_: datetime,
    to: datetime,
    tz: str,
    model_id: int | None,
    org_id: int | None,
    app_id: int | None,
    source: UsageSource | None,
) -> UsageDailyOut:
    from_, to = _validate_range(from_, to)
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError) as exc:
        raise ValidationError("时区不正确") from exc

    conds = await _conditions(session, ctx, from_, to, model_id, org_id, app_id, source)
    calls, failures, input_tokens, output_tokens = _aggregates()
    day = sa.cast(sa.func.date_trunc("day", sa.func.timezone(tz, ModelUsage.at)), sa.Date).label(
        "day"
    )
    query = (
        sa.select(day, calls, failures, input_tokens, output_tokens)
        .select_from(ModelUsage)
        .where(*conds)
        .group_by(day)
    )
    rows = (await session.execute(query)).mappings().all()
    by_day = {row["day"]: row for row in rows}

    # 按 tz 换算 from、to 的本地日期；to 为开区间，最后一天取 to 前一刻的本地日期。
    start = from_.astimezone(zone).date()
    end = (to - timedelta(microseconds=1)).astimezone(zone).date()
    items: list[UsageDailyItem] = []
    current = start
    while current <= end:
        row = by_day.get(current)
        items.append(
            UsageDailyItem(
                date=current,
                calls=int(row["calls"]) if row is not None else 0,
                failures=int(row["failures"]) if row is not None else 0,
                input_tokens=int(row["input_tokens"]) if row is not None else 0,
                output_tokens=int(row["output_tokens"]) if row is not None else 0,
            )
        )
        current += timedelta(days=1)
    return UsageDailyOut(items=items)


def meta() -> UsageMetaOut:
    return UsageMetaOut(
        sources=[
            UsageSourceOption(code=code, label=label) for code, label in USAGE_SOURCE_LABELS.items()
        ]
    )
