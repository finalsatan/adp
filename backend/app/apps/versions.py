"""应用发布、发布历史与恢复（应用技术方案 5.2）。

发布在同一个事务里插版本、改 `apps.published_version_id`、重算 `resource_refs`，要么都成功
要么都回滚。恢复历史版本只把该版本配置复制成草稿，不检查引用是否有效（再发布时由发布校验
拦下）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import config_hash
from app.apps.draft import draft_out, get_app_for_update, parse_config
from app.apps.refs import recompute_for_app
from app.apps.schemas import AppVersionOut, DraftOut, PublishIn
from app.apps.tables import App, AppVersion
from app.apps.validation import validate_publish
from app.core.errors import NotFoundError, PublishCheckFailedError
from app.core.schemas import Page, Pagination, UserBrief
from app.iam.context import Ctx
from app.iam.tables import User
from app.resources import access

APP_TYPE = "app"


def _now() -> datetime:
    return datetime.now(UTC)


async def _get_app(session: AsyncSession, app_id: int) -> App:
    app = await session.get(App, app_id)
    if app is None:
        raise NotFoundError()
    return app


async def _get_version(session: AsyncSession, app_id: int, version_no: int) -> AppVersion:
    version = await session.scalar(
        sa.select(AppVersion).where(
            AppVersion.app_id == app_id, AppVersion.version_no == version_no
        )
    )
    if version is None:
        raise NotFoundError()
    return version


async def version_out(
    session: AsyncSession, app: App, version: AppVersion, *, is_current: bool
) -> AppVersionOut:
    publisher = await session.get(User, version.published_by)
    if publisher is None:
        raise NotFoundError()
    return AppVersionOut(
        id=version.id,
        version_no=version.version_no,
        note=version.note,
        config=parse_config(app.app_type, version.config),
        config_hash=version.config_hash,
        published_by=UserBrief(id=publisher.id, username=publisher.username, name=publisher.name),
        published_at=version.published_at,
        is_current=is_current,
    )


async def publish(session: AsyncSession, ctx: Ctx, app_id: int, body: PublishIn) -> AppVersionOut:
    """发布：先锁应用行（避免并发发布产生重复版本号），再校验、插版本、换指针、重算引用。"""
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    app = await get_app_for_update(session, app_id)

    problems = await validate_publish(session, app)
    if problems:
        raise PublishCheckFailedError([problem.model_dump() for problem in problems])

    config = parse_config(app.app_type, app.draft_config)
    max_no = await session.scalar(
        sa.select(sa.func.max(AppVersion.version_no)).where(AppVersion.app_id == app_id)
    )
    version = AppVersion(
        app_id=app_id,
        version_no=(max_no or 0) + 1,
        config=config.model_dump(mode="json"),
        config_hash=config_hash(config),
        note=body.note,
        published_by=ctx.user_id,
    )
    session.add(version)
    await session.flush()

    app.published_version_id = version.id
    await recompute_for_app(session, app)
    await session.commit()
    await session.refresh(version)
    return await version_out(session, app, version, is_current=True)


async def list_versions(
    session: AsyncSession, ctx: Ctx, app_id: int, pagination: Pagination
) -> Page[AppVersionOut]:
    await access.get_for(session, ctx, app_id, access.VIEW, type=APP_TYPE)
    app = await _get_app(session, app_id)
    stmt = sa.select(AppVersion).where(AppVersion.app_id == app_id)
    total = int(await session.scalar(sa.select(sa.func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(AppVersion.version_no.desc())
                .offset((pagination.page - 1) * pagination.page_size)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        await version_out(session, app, version, is_current=version.id == app.published_version_id)
        for version in rows
    ]
    return Page(items=items, total=total)


async def get_version(
    session: AsyncSession, ctx: Ctx, app_id: int, version_no: int
) -> AppVersionOut:
    await access.get_for(session, ctx, app_id, access.VIEW, type=APP_TYPE)
    app = await _get_app(session, app_id)
    version = await _get_version(session, app_id, version_no)
    return await version_out(
        session, app, version, is_current=version.id == app.published_version_id
    )


async def restore_version(
    session: AsyncSession, ctx: Ctx, app_id: int, version_no: int
) -> DraftOut:
    """把历史版本配置复制成草稿；不检查引用是否有效（再发布时由发布校验拦下）。"""
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    app = await get_app_for_update(session, app_id)
    version = await _get_version(session, app_id, version_no)

    app.draft_config = version.config
    app.draft_revision += 1
    app.draft_updated_by = ctx.user_id
    app.draft_updated_at = _now()
    await recompute_for_app(session, app)
    await session.commit()
    await session.refresh(app)
    return await draft_out(session, app)
