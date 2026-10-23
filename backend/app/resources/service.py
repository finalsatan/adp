"""资源公共业务逻辑：创建、总览、可见范围、协作者、启停与删除。

路由只做参数接收、权限依赖、调用这里的函数和写审计。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AlreadyMemberError,
    NameConflictError,
    NotFoundError,
    OrgRequiredError,
    PermissionDeniedError,
    ResourceInUseError,
    VisibilityFixedError,
)
from app.core.schemas import (
    OrgBrief,
    Page,
    Pagination,
    ResourceStatus,
    Role,
    UserBrief,
    UserStatus,
    Visibility,
)
from app.iam.context import Ctx
from app.iam.permissions import RESOURCE_CREATE
from app.iam.schemas import UserSearchOut
from app.iam.tables import Org, User
from app.resources import access, registry
from app.resources.registry import RefBlocker
from app.resources.schemas import MemberOut, ResourceOut
from app.resources.tables import Resource, ResourceMember, ResourceRef

_VISIBILITY_RANK = {
    Visibility.private.value: 0,
    Visibility.org.value: 1,
    Visibility.platform.value: 2,
}


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _now() -> datetime:
    return datetime.now(UTC)


def _type_label(code: str) -> str:
    resource_type = registry.get_resource_type(code)
    return resource_type.label if resource_type is not None else code


def _build_out(resource: Resource, org: Org, owner: User) -> ResourceOut:
    return ResourceOut(
        id=resource.id,
        type=resource.type,
        type_label=_type_label(resource.type),
        name=resource.name,
        description=resource.description,
        org=OrgBrief(id=org.id, name=org.name),
        owner=UserBrief(id=owner.id, username=owner.username, name=owner.name),
        visibility=Visibility(resource.visibility),
        status=ResourceStatus(resource.status),
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )


async def resource_out(session: AsyncSession, resource: Resource) -> ResourceOut:
    org = await session.get(Org, resource.org_id)
    owner = await session.get(User, resource.owner_id)
    assert org is not None and owner is not None
    return _build_out(resource, org, owner)


async def create_resource(
    session: AsyncSession,
    ctx: Ctx,
    type: str,
    name: str,
    visibility: Visibility,
    description: str | None = None,
    org_id: int | None = None,
) -> Resource:
    """创建资源；第一期没有调用方，第三期起资源模块用。"""
    if RESOURCE_CREATE not in ctx.perms:
        raise PermissionDeniedError()

    if ctx.role == Role.sys_admin:
        # 系统管理员不属于任何组织，必须显式指定。
        if org_id is None:
            raise OrgRequiredError()
    else:
        # 非系统管理员取本人组织，忽略请求里的 org_id。
        org_id = ctx.org_id
        if org_id is None:
            raise PermissionDeniedError()

    org = await session.get(Org, org_id)
    if org is None or org.deleted_at is not None:
        raise NotFoundError()

    resource_type = registry.get_resource_type(type)
    if resource_type is not None and resource_type.fixed_visibility is not None:
        visibility = Visibility(resource_type.fixed_visibility)

    conflict = await session.scalar(
        sa.select(Resource.id).where(
            Resource.type == type,
            Resource.org_id == org.id,
            Resource.name == name,
            Resource.deleted_at.is_(None),
        )
    )
    if conflict is not None:
        raise NameConflictError()

    resource = Resource(
        type=type,
        name=name,
        description=description,
        org_id=org.id,
        owner_id=ctx.user_id,
        visibility=visibility.value,
        status=ResourceStatus.enabled.value,
    )
    session.add(resource)
    await session.commit()
    await session.refresh(resource)
    return resource


async def list_resources(
    session: AsyncSession,
    ctx: Ctx,
    pagination: Pagination,
    type: str | None,
    org_id: int | None,
    include_sub: bool,
    owner_id: int | None,
    visibility: Visibility | None,
    status: ResourceStatus | None,
    keyword: str | None,
) -> Page[ResourceOut]:
    """资源总览：系统管理员看全部，组织管理员看管理范围内的资源。"""
    stmt = (
        sa.select(Resource, Org, User)
        .join(Org, Resource.org_id == Org.id)
        .join(User, Resource.owner_id == User.id)
        .where(Resource.deleted_at.is_(None))
    )

    if ctx.role != Role.sys_admin:
        if ctx.org_path is None:
            return Page(items=[], total=0)
        stmt = stmt.where(Org.path.like(f"{ctx.org_path}%"))

    if type is not None:
        stmt = stmt.where(Resource.type == type)
    if org_id is not None:
        org = await session.get(Org, org_id)
        if org is None or org.deleted_at is not None:
            raise NotFoundError()
        if include_sub:
            stmt = stmt.where(Org.path.like(f"{org.path}%"))
        else:
            stmt = stmt.where(Resource.org_id == org.id)
    if owner_id is not None:
        stmt = stmt.where(Resource.owner_id == owner_id)
    if visibility is not None:
        stmt = stmt.where(Resource.visibility == visibility.value)
    if status is not None:
        stmt = stmt.where(Resource.status == status.value)
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
    return Page(items=[_build_out(*row) for row in rows], total=total)


def _blockers_to_refs(blockers: list[RefBlocker]) -> list[dict]:
    return [{"id": b.id, "type": b.type, "name": b.name} for b in blockers]


async def _run_ref_checkers(
    session: AsyncSession,
    resource: Resource,
    new_visibility: str | None,
    removed_user_id: int | None,
) -> list[dict]:
    refs: list[dict] = []
    for checker in registry.list_ref_checkers():
        found = await checker(session, resource, new_visibility, removed_user_id)
        refs.extend(_blockers_to_refs(found or []))
    return refs


async def change_visibility(
    session: AsyncSession, ctx: Ctx, resource_id: int, visibility: Visibility
) -> tuple[Resource, dict]:
    """修改可见范围；缩小范围前做引用检查。"""
    resource = await access.get_for(session, ctx, resource_id, access.MANAGE)
    resource_type = registry.get_resource_type(resource.type)
    if resource_type is not None and resource_type.fixed_visibility is not None:
        raise VisibilityFixedError()

    old = Visibility(resource.visibility)
    new = Visibility(visibility)
    if new != old:
        if _VISIBILITY_RANK[new.value] < _VISIBILITY_RANK[old.value]:
            refs = await _run_ref_checkers(session, resource, new.value, None)
            if refs:
                raise ResourceInUseError(refs)
        resource.visibility = new.value
        resource.updated_at = _now()
        await session.commit()
        await session.refresh(resource)
    return resource, {"from": old.value, "to": new.value}


async def list_members(session: AsyncSession, ctx: Ctx, resource_id: int) -> list[MemberOut]:
    resource = await access.get_for(session, ctx, resource_id, access.VIEW)
    rows = (
        await session.execute(
            sa.select(ResourceMember, User, Org)
            .join(User, ResourceMember.user_id == User.id)
            .join(Org, User.org_id == Org.id, isouter=True)
            .where(ResourceMember.resource_id == resource.id)
            .order_by(ResourceMember.added_at, ResourceMember.user_id)
        )
    ).all()

    added_by_ids = {member.added_by for member, _, _ in rows}
    added_by: dict[int, UserBrief] = {}
    if added_by_ids:
        creators = (
            await session.execute(
                sa.select(User.id, User.username, User.name).where(User.id.in_(added_by_ids))
            )
        ).all()
        added_by = {
            user_id: UserBrief(id=user_id, username=username, name=name)
            for user_id, username, name in creators
        }

    return [
        MemberOut(
            user=UserSearchOut(
                id=user.id,
                username=user.username,
                name=user.name,
                org=OrgBrief(id=org.id, name=org.name) if org is not None else None,
            ),
            added_by=added_by[member.added_by],
            added_at=member.added_at,
        )
        for member, user, org in rows
    ]


async def _member_out(session: AsyncSession, member: ResourceMember) -> MemberOut:
    user = await session.get(User, member.user_id)
    assert user is not None
    org = await session.get(Org, user.org_id) if user.org_id is not None else None
    adder = await session.get(User, member.added_by)
    assert adder is not None
    return MemberOut(
        user=UserSearchOut(
            id=user.id,
            username=user.username,
            name=user.name,
            org=OrgBrief(id=org.id, name=org.name) if org is not None else None,
        ),
        added_by=UserBrief(id=adder.id, username=adder.username, name=adder.name),
        added_at=member.added_at,
    )


async def add_member(
    session: AsyncSession, ctx: Ctx, resource_id: int, user_id: int
) -> tuple[MemberOut, Resource]:
    resource = await access.get_for(session, ctx, resource_id, access.MANAGE)
    user = await session.get(User, user_id)
    if user is None or user.status != UserStatus.active.value:
        raise NotFoundError()

    existing = await session.get(ResourceMember, {"resource_id": resource.id, "user_id": user.id})
    if existing is not None:
        raise AlreadyMemberError()

    member = ResourceMember(resource_id=resource.id, user_id=user.id, added_by=ctx.user_id)
    session.add(member)
    resource.updated_at = _now()
    await session.commit()
    await session.refresh(member)
    return await _member_out(session, member), resource


async def remove_member(
    session: AsyncSession, ctx: Ctx, resource_id: int, user_id: int
) -> Resource:
    resource = await access.get_for(session, ctx, resource_id, access.MANAGE)
    member = await session.get(ResourceMember, {"resource_id": resource.id, "user_id": user_id})
    if member is None:
        raise NotFoundError()

    refs = await _run_ref_checkers(session, resource, None, user_id)
    if refs:
        raise ResourceInUseError(refs)

    await session.delete(member)
    resource.updated_at = _now()
    await session.commit()
    return resource


async def set_status(session: AsyncSession, ctx: Ctx, resource_id: int, enabled: bool) -> Resource:
    resource = await access.get_for(session, ctx, resource_id, access.MANAGE)
    target = ResourceStatus.enabled.value if enabled else ResourceStatus.disabled.value
    if resource.status != target:
        resource.status = target
        resource.updated_at = _now()
        resource_type = registry.get_resource_type(resource.type)
        if resource_type is not None and resource_type.on_status_change is not None:
            await resource_type.on_status_change(session, resource)
        await session.commit()
        await session.refresh(resource)
    return resource


async def _referencing_resources(session: AsyncSession, resource_id: int) -> list[dict]:
    rows = (
        await session.execute(
            sa.select(Resource)
            .join(ResourceRef, ResourceRef.ref_resource_id == Resource.id)
            .where(ResourceRef.target_type == "resource", ResourceRef.target_id == resource_id)
        )
    ).scalars()
    return [{"id": r.id, "type": r.type, "name": r.name} for r in rows]


async def delete_resource(session: AsyncSession, ctx: Ctx, resource_id: int) -> Resource:
    resource = await access.get_for(session, ctx, resource_id, access.MANAGE)

    refs = await _referencing_resources(session, resource.id)
    if refs:
        raise ResourceInUseError(refs)

    resource_type = registry.get_resource_type(resource.type)
    if resource_type is not None and resource_type.on_delete is not None:
        await resource_type.on_delete(session, resource)

    resource.deleted_at = _now()
    resource.updated_at = _now()
    await session.commit()
    return resource
