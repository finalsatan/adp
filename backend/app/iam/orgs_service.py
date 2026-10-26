"""组织管理业务逻辑：列表、新建、修改、删除与成员列表。"""

from datetime import UTC, datetime

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import (
    NameConflictError,
    NotFoundError,
    OrgNotEmptyError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.schemas import Page, Pagination, Role, UserBrief, UserStatus
from app.iam.context import Ctx, bump_perm_version
from app.iam.schemas import OrgCreate, OrgMemberOut, OrgOut, OrgPatch, OrgTreeOut
from app.iam.scope import can_manage_account, in_scope, strictly_below
from app.iam.tables import Org, User
from app.resources.tables import Resource


def _counts_stmt() -> sa.Select:
    """组织行 + 直属用户数、直属未删除资源数、未删除直接下级数（聚合子查询）。"""
    child = aliased(Org)
    user_count = (
        select(func.count()).select_from(User).where(User.org_id == Org.id).scalar_subquery()
    )
    resource_count = (
        select(func.count())
        .select_from(Resource)
        .where(Resource.org_id == Org.id, Resource.deleted_at.is_(None))
        .scalar_subquery()
    )
    child_count = (
        select(func.count())
        .select_from(child)
        .where(child.parent_id == Org.id, child.deleted_at.is_(None))
        .scalar_subquery()
    )
    return select(
        Org,
        user_count.label("user_count"),
        resource_count.label("resource_count"),
        child_count.label("child_count"),
    )


async def _admins_by_org(session: AsyncSession, org_ids: list[int]) -> dict[int, list[UserBrief]]:
    """各组织的直属、启用的组织管理员，按用户名排序。"""
    if not org_ids:
        return {}
    result = await session.execute(
        select(User.org_id, User.id, User.username, User.name)
        .where(
            User.org_id.in_(org_ids),
            User.role == Role.org_admin.value,
            User.status == UserStatus.active.value,
        )
        .order_by(User.username)
    )
    admins: dict[int, list[UserBrief]] = {}
    for org_id, user_id, username, name in result.all():
        admins.setdefault(org_id, []).append(UserBrief(id=user_id, username=username, name=name))
    return admins


def _editable(ctx: Ctx, org: Org) -> bool:
    """系统管理员恒为真；组织管理员仅对严格下级为真。"""
    if ctx.role == Role.sys_admin:
        return True
    return strictly_below(ctx, org.path)


def _build_org_out(
    ctx: Ctx,
    org: Org,
    user_count: int,
    resource_count: int,
    child_count: int,
    admins: list[UserBrief],
) -> OrgOut:
    return OrgOut(
        id=org.id,
        parent_id=org.parent_id,
        path=org.path,
        name=org.name,
        remark=org.remark,
        admins=admins,
        user_count=user_count,
        resource_count=resource_count,
        child_count=child_count,
        created_at=org.created_at,
        editable=_editable(ctx, org),
    )


async def _org_out(session: AsyncSession, ctx: Ctx, org: Org) -> OrgOut:
    row = (await session.execute(_counts_stmt().where(Org.id == org.id))).one()
    _, user_count, resource_count, child_count = row
    admins = (await _admins_by_org(session, [org.id])).get(org.id, [])
    return _build_org_out(ctx, org, user_count, resource_count, child_count, admins)


async def _load_org(session: AsyncSession, org_id: int) -> Org:
    org = await session.get(Org, org_id)
    if org is None or org.deleted_at is not None:
        raise NotFoundError()
    return org


async def _name_conflict(
    session: AsyncSession, parent_id: int | None, name: str, exclude_id: int | None = None
) -> bool:
    """同一上级下（顶级组织之间）未删除的组织是否重名。"""
    stmt = select(Org.id).where(Org.deleted_at.is_(None), Org.name == name)
    stmt = stmt.where(Org.parent_id.is_(None) if parent_id is None else Org.parent_id == parent_id)
    if exclude_id is not None:
        stmt = stmt.where(Org.id != exclude_id)
    return await session.scalar(stmt) is not None


async def list_orgs(session: AsyncSession, ctx: Ctx) -> list[OrgOut]:
    """系统管理员看全部；组织管理员看管理范围内（含本组织）的未删除组织。"""
    stmt = _counts_stmt().where(Org.deleted_at.is_(None)).order_by(Org.path)
    if ctx.role != Role.sys_admin:
        if ctx.org_path is None:
            return []
        stmt = stmt.where(Org.path.like(f"{ctx.org_path}%"))

    rows = (await session.execute(stmt)).all()
    orgs = [row[0] for row in rows]
    admins = await _admins_by_org(session, [org.id for org in orgs])
    return [
        _build_org_out(ctx, org, user_count, resource_count, child_count, admins.get(org.id, []))
        for org, user_count, resource_count, child_count in rows
    ]


async def list_org_tree(session: AsyncSession, ctx: Ctx) -> list[OrgTreeOut]:
    """只读组织树：系统管理员看全部，其余角色看本人组织子树（含本组织）。

    只返回 id、parent_id、名称，供“使用范围→指定组织”这类选择器使用；
    不返回成员、管理员等管理信息，也不要求 org.manage。
    """
    stmt = (
        select(Org.id, Org.parent_id, Org.name).where(Org.deleted_at.is_(None)).order_by(Org.path)
    )
    if ctx.role != Role.sys_admin:
        if ctx.org_path is None:
            return []
        stmt = stmt.where(Org.path.like(f"{ctx.org_path}%"))

    rows = (await session.execute(stmt)).all()
    return [
        OrgTreeOut(id=org_id, parent_id=parent_id, name=name) for org_id, parent_id, name in rows
    ]


async def create_org(session: AsyncSession, ctx: Ctx, body: OrgCreate) -> OrgOut:
    name = body.name.strip()
    if not name:
        raise ValidationError("组织名称不能为空")

    parent: Org | None = None
    if body.parent_id is None:
        # 只有系统管理员能建顶级组织。
        if ctx.role != Role.sys_admin:
            raise PermissionDeniedError()
    else:
        parent = await _load_org(session, body.parent_id)
        if not in_scope(ctx, parent.path):
            raise PermissionDeniedError()

    if await _name_conflict(session, body.parent_id, name):
        raise NameConflictError()

    org_id = await session.scalar(sa.text("SELECT nextval('orgs_id_seq')"))
    assert org_id is not None
    org = Org(
        id=org_id,
        parent_id=body.parent_id,
        path=f"{parent.path if parent is not None else '/'}{org_id}/",
        name=name,
        remark=body.remark,
        created_by=ctx.user_id,
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return await _org_out(session, ctx, org)


async def patch_org(
    session: AsyncSession,
    redis: Redis,
    ctx: Ctx,
    org_id: int,
    body: OrgPatch,
) -> tuple[OrgOut, dict]:
    """修改名称和备注；返回组织与变化字段（供审计 detail）。"""
    org = await _load_org(session, org_id)
    if not _editable(ctx, org):
        raise PermissionDeniedError()

    changes: dict = {}
    if "name" in body.model_fields_set:
        name = (body.name or "").strip()
        if not name:
            raise ValidationError("组织名称不能为空")
        if name != org.name:
            if await _name_conflict(session, org.parent_id, name, exclude_id=org.id):
                raise NameConflictError()
            changes["name"] = [org.name, name]
            org.name = name
    if "remark" in body.model_fields_set and body.remark != org.remark:
        changes["remark"] = [org.remark, body.remark]
        org.remark = body.remark

    if changes:
        await session.commit()
    if "name" in changes:
        # 缓存的用户上下文里有组织名，改名后立即失效。
        await bump_perm_version(redis)

    return await _org_out(session, ctx, org), changes


async def delete_org(session: AsyncSession, ctx: Ctx, org_id: int) -> Org:
    """软删除；返回被删除的组织（供审计对象描述）。"""
    org = await _load_org(session, org_id)
    if not _editable(ctx, org):
        raise PermissionDeniedError()

    child = aliased(Org)
    row = (
        await session.execute(
            select(
                select(func.count())
                .select_from(child)
                .where(child.parent_id == org.id, child.deleted_at.is_(None))
                .scalar_subquery(),
                select(func.count())
                .select_from(User)
                .where(User.org_id == org.id)
                .scalar_subquery(),
                select(func.count())
                .select_from(Resource)
                .where(Resource.org_id == org.id, Resource.deleted_at.is_(None))
                .scalar_subquery(),
            )
        )
    ).one()
    children, users, resources = (int(value or 0) for value in row)
    if children or users or resources:
        raise OrgNotEmptyError(children, users, resources)

    org.deleted_at = datetime.now(UTC)
    await session.commit()
    return org


async def list_members(
    session: AsyncSession, ctx: Ctx, org_id: int, pagination: Pagination
) -> Page[OrgMemberOut]:
    """只列直属该组织的用户，按用户名排序分页。"""
    org = await _load_org(session, org_id)
    if not in_scope(ctx, org.path):
        raise PermissionDeniedError()

    total = await session.scalar(
        select(func.count()).select_from(User).where(User.org_id == org.id)
    )
    users = (
        (
            await session.execute(
                select(User)
                .where(User.org_id == org.id)
                .order_by(User.username)
                .offset((pagination.page - 1) * pagination.page_size)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        OrgMemberOut(
            id=user.id,
            username=user.username,
            name=user.name,
            role=Role(user.role),
            status=UserStatus(user.status),
            manageable=can_manage_account(ctx, Role(user.role), org.path, user.id),
        )
        for user in users
    ]
    return Page(items=items, total=int(total or 0))
