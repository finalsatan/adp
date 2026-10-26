"""应用草稿：读取与保存（应用技术方案 5.2）。

保存草稿只做格式校验（`AppConfig` 能解析）和“重排要求”检查，允许不完整配置；保存不写
审计（应用技术方案 14）。草稿哈希用 P4-03 的 `config_hash`。
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import config_hash
from app.apps.refs import recompute_for_app
from app.apps.schemas import DraftIn, DraftOut
from app.apps.tables import App, AppVersion
from app.apps.validation import check_rerank, parse_config, raise_if_rerank_problems
from app.core.errors import DraftConflictError, NotFoundError
from app.core.schemas import UserBrief
from app.iam.context import Ctx
from app.iam.tables import User
from app.resources import access

APP_TYPE = "app"


def _now() -> datetime:
    return datetime.now(UTC)


async def draft_out(session: AsyncSession, app: App) -> DraftOut:
    """拼草稿返回结构。`has_changes` = 草稿哈希与当前发布版本哈希不同（没有版本时为 true）。"""
    published = (
        await session.get(AppVersion, app.published_version_id)
        if app.published_version_id is not None
        else None
    )
    has_changes = published is None or config_hash(app.draft_config) != published.config_hash
    updated_by = None
    if app.draft_updated_by is not None:
        user = await session.get(User, app.draft_updated_by)
        if user is not None:
            updated_by = UserBrief(id=user.id, username=user.username, name=user.name)
    return DraftOut(
        draft_revision=app.draft_revision,
        config=parse_config(app.app_type, app.draft_config),
        has_changes=has_changes,
        updated_by=updated_by,
        updated_at=app.draft_updated_at,
    )


async def _get_app(session: AsyncSession, app_id: int) -> App:
    app = await session.get(App, app_id)
    if app is None:
        raise NotFoundError()
    return app


async def get_app_for_update(session: AsyncSession, app_id: int) -> App:
    """`SELECT ... FOR UPDATE` 锁住 apps 行，串行化对同一应用的草稿保存、发布与恢复。

    乐观锁的 revision 比较必须在这个行锁之后，否则两个请求会同时读到旧 revision，
    都通过比较，后提交的覆盖前者（应用技术方案 17.3）。
    """
    app = await session.scalar(
        sa.select(App)
        .where(App.id == app_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if app is None:
        raise NotFoundError()
    return app


async def get_draft(session: AsyncSession, ctx: Ctx, app_id: int) -> DraftOut:
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    return await draft_out(session, await _get_app(session, app_id))


async def save_draft(session: AsyncSession, ctx: Ctx, app_id: int, body: DraftIn) -> DraftOut:
    """保存草稿：行锁 + 乐观锁 + 重排要求检查；重新计算引用登记。"""
    await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    app = await get_app_for_update(session, app_id)
    if app.draft_revision != body.draft_revision:
        raise DraftConflictError()

    raise_if_rerank_problems(await check_rerank(session, body.config))

    app.draft_config = body.config.model_dump(mode="json")
    app.draft_revision += 1
    app.draft_updated_by = ctx.user_id
    app.draft_updated_at = _now()
    await recompute_for_app(session, app)
    await session.commit()
    await session.refresh(app)
    return await draft_out(session, app)
