"""使用范围判断、使用范围读写与“我的应用”（P4-05，应用技术方案 11）。

`app_usable_by_user` / `get_chat_app` 是终端用户判断的统一入口，P4-09（会话与提问）、
P4-10（引用预览）直接复用，不要各写一套；每次实时计算，不做缓存（使用范围修改后
立即生效，不需要重新发布）。

使用范围与可见范围分开：可见范围控制台，使用范围管提问（需求 5.4.1）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.schemas import (
    MyAppOut,
    UsageScopeIn,
    UsageScopeOut,
    UsageScopeType,
    UsageTargetType,
)
from app.apps.tables import App, AppUsageTarget
from app.core.errors import (
    AppDisabledError,
    AppNotInScopeError,
    AppNotPublishedError,
    NotFoundError,
)
from app.core.schemas import (
    OrgBrief,
    ResourceStatus,
    UserBrief,
    UserStatus,
)
from app.iam.context import Ctx
from app.iam.tables import Org, User
from app.resources import access
from app.resources.tables import Resource

APP_TYPE = "app"


def _scope_clause(user_id: int, org_path: str | None) -> sa.ColumnElement[bool]:
    """使用范围匹配条件（不含发布、停用、用户状态）；放进 `App` 的 WHERE。

    `platform`：所有登录用户；`orgs`：用户组织 path 以名单中任一组织 path 开头（含下级）；
    `users`：用户在名单中。系统管理员不属于任何组织，`orgs` 永远匹配不上。
    """
    clauses: list[sa.ColumnElement[bool]] = [App.usage_scope == UsageScopeType.platform.value]
    if org_path is not None:
        clauses.append(
            sa.and_(
                App.usage_scope == UsageScopeType.orgs.value,
                sa.exists(
                    sa.select(AppUsageTarget.app_id)
                    .join(Org, Org.id == AppUsageTarget.target_id)
                    .where(
                        AppUsageTarget.app_id == App.id,
                        AppUsageTarget.target_type == UsageTargetType.org.value,
                        sa.literal(org_path).like(Org.path + "%"),
                    )
                ),
            )
        )
    clauses.append(
        sa.and_(
            App.usage_scope == UsageScopeType.users.value,
            sa.exists(
                sa.select(AppUsageTarget.app_id).where(
                    AppUsageTarget.app_id == App.id,
                    AppUsageTarget.target_type == UsageTargetType.user.value,
                    AppUsageTarget.target_id == user_id,
                )
            ),
        )
    )
    return sa.or_(*clauses)


async def _org_path(session: AsyncSession, org_id: int | None) -> str | None:
    if org_id is None:
        return None
    return await session.scalar(sa.select(Org.path).where(Org.id == org_id))


async def app_usable_by_user(session: AsyncSession, app: App, user: User) -> bool:
    """用户能否使用该应用（应用技术方案 11）。

    应用已发布、未停用、未删除；用户状态启用；并满足使用范围之一。不看角色。
    """
    if user.status != UserStatus.active.value:
        return False
    resource = await session.get(Resource, app.id)
    if resource is None or resource.deleted_at is not None or resource.type != APP_TYPE:
        return False
    if resource.status != ResourceStatus.enabled.value:
        return False
    if app.published_version_id is None:
        return False
    if app.usage_scope == UsageScopeType.platform.value:
        return True
    org_path = await _org_path(session, user.org_id)
    matched = await session.scalar(
        sa.select(App.id).where(App.id == app.id, _scope_clause(user.id, org_path))
    )
    return matched is not None


async def get_chat_app(session: AsyncSession, ctx: Ctx, app_id: int) -> tuple[App, Resource]:
    """终端用户打开对话页用；返回 `(app, resource)` 或抛对应错误。

    不存在或已删除 → 404；未发布 → 403 `APP_NOT_PUBLISHED`；已停用 → 403 `APP_DISABLED`；
    不在范围 → 403 `APP_NOT_IN_SCOPE`。P4-09、P4-10 每次提问 / 读取会话 / 预览都重新调用。
    """
    resource = await session.get(Resource, app_id)
    if resource is None or resource.deleted_at is not None or resource.type != APP_TYPE:
        raise NotFoundError()
    app = await session.get(App, app_id)
    if app is None:
        raise NotFoundError()
    user = await session.get(User, ctx.user_id)
    if user is None or user.status != UserStatus.active.value:
        raise AppNotInScopeError()
    if app.published_version_id is None:
        raise AppNotPublishedError()
    if resource.status != ResourceStatus.enabled.value:
        raise AppDisabledError()
    if not await app_usable_by_user(session, app, user):
        raise AppNotInScopeError()
    return app, resource


async def list_my_apps(session: AsyncSession, ctx: Ctx) -> list[MyAppOut]:
    """“我的应用”：已发布、未停用、未删除、用户启用且在范围内的应用，按名称排序。"""
    if ctx.status != UserStatus.active.value:
        return []
    rows = (
        await session.execute(
            sa.select(Resource, Org, App)
            .join(Org, Resource.org_id == Org.id)
            .join(App, App.id == Resource.id)
            .where(
                Resource.type == APP_TYPE,
                Resource.deleted_at.is_(None),
                Resource.status == ResourceStatus.enabled.value,
                App.published_version_id.is_not(None),
                _scope_clause(ctx.user_id, ctx.org_path),
            )
            .order_by(Resource.name)
        )
    ).all()
    return [
        MyAppOut(
            id=resource.id,
            name=resource.name,
            description=resource.description,
            app_type=app.app_type,
            org=OrgBrief(id=org.id, name=org.name),
            chat_path=f"/chat/{resource.id}",
        )
        for resource, org, app in rows
    ]


async def _scope_out(session: AsyncSession, app: App) -> UsageScopeOut:
    scope = UsageScopeType(app.usage_scope)
    if scope == UsageScopeType.platform:
        return UsageScopeOut(scope=scope, orgs=[], users=[])
    targets = (
        (
            await session.execute(
                sa.select(AppUsageTarget)
                .where(AppUsageTarget.app_id == app.id)
                .order_by(AppUsageTarget.target_id)
            )
        )
        .scalars()
        .all()
    )
    org_ids = [t.target_id for t in targets if t.target_type == UsageTargetType.org.value]
    user_ids = [t.target_id for t in targets if t.target_type == UsageTargetType.user.value]
    orgs: list[OrgBrief] = []
    if org_ids:
        rows = (await session.execute(sa.select(Org).where(Org.id.in_(org_ids)))).scalars().all()
        orgs = [OrgBrief(id=org.id, name=org.name) for org in rows]
    users: list[UserBrief] = []
    if user_ids:
        rows = (await session.execute(sa.select(User).where(User.id.in_(user_ids)))).scalars().all()
        users = [UserBrief(id=user.id, username=user.username, name=user.name) for user in rows]
    return UsageScopeOut(scope=scope, orgs=orgs, users=users)


async def get_usage_scope(session: AsyncSession, ctx: Ctx, app_id: int) -> UsageScopeOut:
    """读取使用范围；要求应用“查看”权限。"""
    await access.get_for(session, ctx, app_id, access.VIEW, type=APP_TYPE)
    app = await session.get(App, app_id)
    if app is None:
        raise NotFoundError()
    return await _scope_out(session, app)


async def set_usage_scope(
    session: AsyncSession, ctx: Ctx, app_id: int, body: UsageScopeIn
) -> tuple[UsageScopeOut, dict]:
    """全量替换使用范围；要求应用“管理”权限，返回 `(新范围, 审计 detail)`。

    同一个事务里改 `apps.usage_scope`、先删后插 `app_usage_targets`；修改不需要重新发布。
    """
    await access.get_for(session, ctx, app_id, access.MANAGE, type=APP_TYPE)
    app = await session.get(App, app_id)
    if app is None:
        raise NotFoundError()
    await _check_targets_exist(session, body)
    before = await _scope_out(session, app)

    app.usage_scope = body.scope.value
    await session.execute(sa.delete(AppUsageTarget).where(AppUsageTarget.app_id == app.id))
    for org_id in dict.fromkeys(body.org_ids):
        session.add(
            AppUsageTarget(
                app_id=app.id,
                target_type=UsageTargetType.org.value,
                target_id=org_id,
            )
        )
    for user_id in dict.fromkeys(body.user_ids):
        session.add(
            AppUsageTarget(
                app_id=app.id,
                target_type=UsageTargetType.user.value,
                target_id=user_id,
            )
        )
    await session.commit()
    await session.refresh(app)
    after = await _scope_out(session, app)
    detail = {
        "from": before.model_dump(mode="json"),
        "to": after.model_dump(mode="json"),
    }
    return after, detail


async def _check_targets_exist(session: AsyncSession, body: UsageScopeIn) -> None:
    """组织、用户必须存在且未删除。"""
    if body.org_ids:
        found = set(
            (
                await session.execute(
                    sa.select(Org.id).where(Org.id.in_(body.org_ids), Org.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        if found != set(body.org_ids):
            raise NotFoundError()
    if body.user_ids:
        found = set(
            (await session.execute(sa.select(User.id).where(User.id.in_(body.user_ids))))
            .scalars()
            .all()
        )
        if found != set(body.user_ids):
            raise NotFoundError()
