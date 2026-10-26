"""应用管理业务逻辑（P4-03）。

路由只做参数接收、权限依赖、调用这里、写审计。权限一律复用
`app/resources/access.py`（P1-12），列表过滤拼进 SQL 的 WHERE。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import AppType, config_hash, default_config
from app.apps.schemas import (
    AppCreate,
    AppOut,
    AppStatus,
    AppUpdate,
    AppVersionBrief,
    UsageScopeOut,
    UsageScopeType,
    UsageTargetType,
)
from app.apps.tables import App, AppUsageTarget, AppVersion
from app.core.errors import AppTypeImmutableError, NameConflictError, NotFoundError
from app.core.schemas import (
    OrgBrief,
    Page,
    Pagination,
    ResourceStatus,
    UserBrief,
    Visibility,
)
from app.iam.context import Ctx
from app.iam.tables import Org, User
from app.resources import access
from app.resources.schemas import MemberOut
from app.resources.service import (
    add_member,
    change_visibility,
    create_resource,
    delete_resource,
    list_members,
    remove_member,
    set_status,
)
from app.resources.tables import Resource, ResourceMember

logger = logging.getLogger(__name__)

APP_TYPE = "app"


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _now() -> datetime:
    return datetime.now(UTC)


def _status_of(resource: Resource, app: App) -> AppStatus:
    """由资源启停和 `published_version_id` 推导状态（应用技术方案 10）。"""
    if resource.status == ResourceStatus.disabled.value:
        return AppStatus.disabled
    if app.published_version_id is None:
        return AppStatus.unpublished
    return AppStatus.published


def _draft_has_changes(app: App, published: AppVersion | None) -> bool:
    if published is None:
        return True
    return config_hash(app.draft_config) != published.config_hash


def _build_out(
    resource: Resource,
    org: Org,
    owner: User,
    app: App,
    published: AppVersion | None,
    usage_scope: UsageScopeOut,
    editable: bool,
    manageable: bool,
) -> AppOut:
    return AppOut(
        id=resource.id,
        name=resource.name,
        description=resource.description,
        app_type=AppType(app.app_type),
        org=OrgBrief(id=org.id, name=org.name),
        owner=UserBrief(id=owner.id, username=owner.username, name=owner.name),
        visibility=Visibility(resource.visibility),
        status=_status_of(resource, app),
        published_version=(
            AppVersionBrief(
                id=published.id,
                version_no=published.version_no,
                published_at=published.published_at,
            )
            if published is not None
            else None
        ),
        draft_has_changes=_draft_has_changes(app, published),
        draft_updated_at=app.draft_updated_at,
        usage_scope=usage_scope,
        chat_path=f"/chat/{resource.id}",
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        editable=editable,
        manageable=manageable,
    )


def _flags(
    ctx: Ctx, resource: Resource, org_path: str | None, is_member: bool
) -> tuple[bool, bool]:
    """`editable` / `manageable`：复用 access 的同一套规则，与 `get_for` 结论一致。"""
    return (
        access._can(ctx, access.EDIT, resource, org_path, is_member),
        access._can(ctx, access.MANAGE, resource, org_path, is_member),
    )


async def _is_member(session: AsyncSession, ctx: Ctx, resource_id: int) -> bool:
    return (
        await session.scalar(
            sa.select(ResourceMember.resource_id).where(
                ResourceMember.resource_id == resource_id,
                ResourceMember.user_id == ctx.user_id,
            )
        )
        is not None
    )


async def _usage_scope_map(session: AsyncSession, apps: list[App]) -> dict[int, UsageScopeOut]:
    """批量取使用范围，避免列表逐行查询。"""
    result = {
        app.id: UsageScopeOut(scope=UsageScopeType(app.usage_scope), orgs=[], users=[])
        for app in apps
    }
    scoped_ids = [app.id for app in apps if app.usage_scope != UsageScopeType.platform.value]
    if not scoped_ids:
        return result

    targets = (
        (
            await session.execute(
                sa.select(AppUsageTarget)
                .where(AppUsageTarget.app_id.in_(scoped_ids))
                .order_by(AppUsageTarget.target_id)
            )
        )
        .scalars()
        .all()
    )
    org_ids = {t.target_id for t in targets if t.target_type == UsageTargetType.org.value}
    user_ids = {t.target_id for t in targets if t.target_type == UsageTargetType.user.value}
    org_briefs: dict[int, OrgBrief] = {}
    if org_ids:
        orgs = (await session.execute(sa.select(Org).where(Org.id.in_(org_ids)))).scalars().all()
        org_briefs = {org.id: OrgBrief(id=org.id, name=org.name) for org in orgs}
    user_briefs: dict[int, UserBrief] = {}
    if user_ids:
        users = (
            (await session.execute(sa.select(User).where(User.id.in_(user_ids)))).scalars().all()
        )
        user_briefs = {
            user.id: UserBrief(id=user.id, username=user.username, name=user.name) for user in users
        }

    grouped: dict[int, list[AppUsageTarget]] = {}
    for target in targets:
        grouped.setdefault(target.app_id, []).append(target)
    for app_id, items in grouped.items():
        orgs = [
            org_briefs[t.target_id]
            for t in items
            if t.target_type == UsageTargetType.org.value and t.target_id in org_briefs
        ]
        users = [
            user_briefs[t.target_id]
            for t in items
            if t.target_type == UsageTargetType.user.value and t.target_id in user_briefs
        ]
        result[app_id] = UsageScopeOut(scope=result[app_id].scope, orgs=orgs, users=users)
    return result


async def _published_versions(session: AsyncSession, apps: list[App]) -> dict[int, AppVersion]:
    ids = {app.published_version_id for app in apps if app.published_version_id is not None}
    if not ids:
        return {}
    versions = (
        (await session.execute(sa.select(AppVersion).where(AppVersion.id.in_(ids)))).scalars().all()
    )
    return {version.id: version for version in versions}


async def list_apps(
    session: AsyncSession,
    ctx: Ctx,
    pagination: Pagination,
    app_type: AppType | None,
    status: AppStatus | None,
    visibility: Visibility | None,
    org_id: int | None,
    include_sub: bool,
    keyword: str | None,
) -> Page[AppOut]:
    """我能查看的应用列表；权限条件拼进 WHERE，按创建时间倒序。"""
    stmt = (
        sa.select(Resource, Org, User, App)
        .join(Org, Resource.org_id == Org.id)
        .join(App, App.id == Resource.id)
        .join(User, Resource.owner_id == User.id)
        .where(
            Resource.type == APP_TYPE,
            Resource.deleted_at.is_(None),
            access.access_clause(ctx, access.VIEW),
        )
    )

    if app_type is not None:
        stmt = stmt.where(App.app_type == app_type.value)
    if status is not None:
        if status == AppStatus.disabled:
            stmt = stmt.where(Resource.status == ResourceStatus.disabled.value)
        elif status == AppStatus.unpublished:
            stmt = stmt.where(
                Resource.status == ResourceStatus.enabled.value,
                App.published_version_id.is_(None),
            )
        else:
            stmt = stmt.where(
                Resource.status == ResourceStatus.enabled.value,
                App.published_version_id.is_not(None),
            )
    if visibility is not None:
        stmt = stmt.where(Resource.visibility == visibility.value)
    if org_id is not None:
        org = await session.get(Org, org_id)
        if org is None or org.deleted_at is not None:
            raise NotFoundError()
        if include_sub:
            stmt = stmt.where(Org.path.like(f"{_escape_like(org.path)}%", escape="\\"))
        else:
            stmt = stmt.where(Resource.org_id == org.id)
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        stmt = stmt.where(Resource.name.ilike(pattern, escape="\\"))

    total = int(await session.scalar(sa.select(sa.func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await session.execute(
            stmt.order_by(Resource.created_at.desc(), Resource.id.desc())
            .offset((pagination.page - 1) * pagination.page_size)
            .limit(pagination.page_size)
        )
    ).all()

    ids = [resource.id for resource, *_ in rows]
    members: set[int] = set()
    if ids:
        members = set(
            (
                await session.execute(
                    sa.select(ResourceMember.resource_id).where(
                        ResourceMember.resource_id.in_(ids),
                        ResourceMember.user_id == ctx.user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    apps = [app for *_, app in rows]
    usage_scopes = await _usage_scope_map(session, apps)
    versions = await _published_versions(session, apps)

    items = []
    for resource, org, owner, app in rows:
        editable, manageable = _flags(ctx, resource, org.path, resource.id in members)
        published = versions.get(app.published_version_id) if app.published_version_id else None
        items.append(
            _build_out(
                resource,
                org,
                owner,
                app,
                published,
                usage_scopes[app.id],
                editable,
                manageable,
            )
        )
    return Page(items=items, total=total)


async def app_out(session: AsyncSession, ctx: Ctx, app: App) -> AppOut:
    """拼单个应用的返回结构。"""
    resource = await session.get(Resource, app.id)
    if resource is None or resource.deleted_at is not None:
        raise NotFoundError()
    org = await session.get(Org, resource.org_id)
    owner = await session.get(User, resource.owner_id)
    assert org is not None and owner is not None
    published = (
        await session.get(AppVersion, app.published_version_id)
        if app.published_version_id is not None
        else None
    )
    usage_scope = (await _usage_scope_map(session, [app]))[app.id]
    editable, manageable = _flags(ctx, resource, org.path, await _is_member(session, ctx, app.id))
    return _build_out(resource, org, owner, app, published, usage_scope, editable, manageable)


async def get_app(session: AsyncSession, ctx: Ctx, app_id: int) -> AppOut:
    await access.get_for(session, ctx, app_id, access.VIEW, type=APP_TYPE)
    app = await session.get(App, app_id)
    if app is None:
        raise NotFoundError()
    return await app_out(session, ctx, app)


async def create_app(session: AsyncSession, ctx: Ctx, body: AppCreate) -> AppOut:
    resource = await create_resource(
        session, ctx, APP_TYPE, body.name, body.visibility, body.description, body.org_id
    )
    try:
        app = App(
            id=resource.id,
            app_type=body.app_type.value,
            draft_config=default_config(body.app_type).model_dump(mode="json"),
            draft_revision=1,
            draft_updated_by=ctx.user_id,
            draft_updated_at=_now(),
            published_version_id=None,
            usage_scope=UsageScopeType.orgs.value,
        )
        session.add(app)
        # 默认使用范围为应用所属组织（应用技术方案 10、11）。
        session.add(
            AppUsageTarget(
                app_id=resource.id,
                target_type=UsageTargetType.org.value,
                target_id=resource.org_id,
            )
        )
        await session.commit()
        await session.refresh(app)
    except Exception:
        await session.rollback()
        await _cleanup_orphan_resource(session, resource.id)
        raise
    return await app_out(session, ctx, app)


async def _cleanup_orphan_resource(session: AsyncSession, resource_id: int) -> None:
    """插 apps 行失败时，把 `create_resource` 已提交的资源软删除，不留空壳应用。"""
    try:
        await session.execute(
            sa.update(Resource)
            .where(Resource.id == resource_id)
            .values(deleted_at=_now(), updated_at=_now())
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.error("清理未建成应用资源的资源失败: resource_id=%s", resource_id, exc_info=True)


async def update_app(session: AsyncSession, ctx: Ctx, app_id: int, body: AppUpdate) -> AppOut:
    resource = await access.get_for(session, ctx, app_id, access.EDIT, type=APP_TYPE)
    if body.app_type is not None:
        raise AppTypeImmutableError()

    if body.name is not None and body.name != resource.name:
        conflict = await session.scalar(
            sa.select(Resource.id).where(
                Resource.type == APP_TYPE,
                Resource.org_id == resource.org_id,
                Resource.name == body.name,
                Resource.deleted_at.is_(None),
                Resource.id != resource.id,
            )
        )
        if conflict is not None:
            raise NameConflictError()
        resource.name = body.name
    if "description" in body.model_fields_set:
        resource.description = body.description
    resource.updated_at = _now()
    await session.commit()
    await session.refresh(resource)

    app = await session.get(App, resource.id)
    if app is None:
        raise NotFoundError()
    return await app_out(session, ctx, app)


async def delete_app(session: AsyncSession, ctx: Ctx, app_id: int) -> Resource:
    return await delete_resource(session, ctx, app_id)


async def set_app_status(session: AsyncSession, ctx: Ctx, app_id: int, enabled: bool) -> AppOut:
    resource = await set_status(session, ctx, app_id, enabled)
    app = await session.get(App, resource.id)
    if app is None:
        raise NotFoundError()
    return await app_out(session, ctx, app)


async def change_app_visibility(
    session: AsyncSession, ctx: Ctx, app_id: int, visibility: Visibility
) -> tuple[AppOut, dict]:
    resource, detail = await change_visibility(session, ctx, app_id, visibility)
    app = await session.get(App, resource.id)
    if app is None:
        raise NotFoundError()
    return await app_out(session, ctx, app), detail


async def list_app_members(session: AsyncSession, ctx: Ctx, app_id: int) -> list[MemberOut]:
    return await list_members(session, ctx, app_id)


async def add_app_member(
    session: AsyncSession, ctx: Ctx, app_id: int, user_id: int
) -> tuple[MemberOut, Resource]:
    return await add_member(session, ctx, app_id, user_id)


async def remove_app_member(session: AsyncSession, ctx: Ctx, app_id: int, user_id: int) -> Resource:
    return await remove_member(session, ctx, app_id, user_id)
