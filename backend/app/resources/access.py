"""资源权限判断（技术方案 7.3、需求 10.4）。

“创建人权限”只在创建人仍属于资源所属组织时有效：上下文里的 org_id 就是
当前用户的组织，所以“有效创建人”等价于 `owner_id == ctx.user_id` 且
`resource.org_id == ctx.org_id`。

`access_clause` 返回可直接放进 WHERE 的条件，调用方的查询要 join
`Org`（`Resource.org_id == Org.id`）取资源组织的 path；协作者用 EXISTS 子查询。
`get_for` 用同样的规则在内存里判断，两者结论一致（测试覆盖）。
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.schemas import Role, Visibility
from app.iam.context import Ctx
from app.iam.tables import Org
from app.resources.tables import Resource, ResourceMember

VIEW = "view"
EDIT = "edit"
MANAGE = "manage"


def _is_owner(ctx: Ctx, resource: Resource) -> bool:
    """有效创建人：创建人本人，且当前仍属于资源所属组织。"""
    return (
        ctx.org_id is not None
        and resource.owner_id == ctx.user_id
        and resource.org_id == ctx.org_id
    )


def _is_scoped_admin(ctx: Ctx, org_path: str | None) -> bool:
    """资源所属组织在操作人的管理范围内（含本组织）。"""
    return (
        ctx.role == Role.org_admin
        and ctx.org_path is not None
        and org_path is not None
        and org_path.startswith(ctx.org_path)
    )


def _org_visible(ctx: Ctx, visibility: str, org_path: str | None) -> bool:
    """可见范围为全平台，或“本组织”且用户组织在资源组织之下。"""
    if visibility == Visibility.platform.value:
        return True
    if visibility == Visibility.org.value:
        return (
            ctx.org_path is not None and org_path is not None and ctx.org_path.startswith(org_path)
        )
    return False


def _can(ctx: Ctx, action: str, resource: Resource, org_path: str | None, is_member: bool) -> bool:
    if ctx.role == Role.sys_admin:
        return True
    if action == VIEW and _org_visible(ctx, resource.visibility, org_path):
        return True
    if action in (VIEW, EDIT) and is_member:
        return True
    if _is_owner(ctx, resource):
        return True
    return _is_scoped_admin(ctx, org_path)


def _member_exists(ctx: Ctx) -> sa.ColumnElement[bool]:
    return (
        sa.select(ResourceMember.resource_id)
        .where(
            ResourceMember.resource_id == Resource.id,
            ResourceMember.user_id == ctx.user_id,
        )
        .exists()
    )


def access_clause(ctx: Ctx, action: str) -> sa.ColumnElement[bool]:
    """按操作返回可放进 WHERE 的权限条件；调用方需 join Org。"""
    if ctx.role == Role.sys_admin:
        return sa.true()

    if ctx.org_id is None:
        owner: sa.ColumnElement[bool] = sa.false()
    else:
        owner = sa.and_(
            Resource.owner_id == ctx.user_id,
            Resource.org_id == ctx.org_id,
        )

    if ctx.role == Role.org_admin and ctx.org_path is not None:
        scoped_admin: sa.ColumnElement[bool] = Org.path.like(f"{ctx.org_path}%")
    else:
        scoped_admin = sa.false()

    if action == VIEW:
        conditions = [scoped_admin, owner, _member_exists(ctx)]
        conditions.append(Resource.visibility == Visibility.platform.value)
        if ctx.org_path is not None:
            conditions.append(
                sa.and_(
                    Resource.visibility == Visibility.org.value,
                    sa.literal(ctx.org_path).like(Org.path + "%"),
                )
            )
        return sa.or_(*conditions)

    if action == EDIT:
        return sa.or_(scoped_admin, owner, _member_exists(ctx))

    return sa.or_(scoped_admin, owner)


async def get_for(
    session: AsyncSession,
    ctx: Ctx,
    resource_id: int,
    action: str,
    type: str | None = None,
) -> Resource:
    """取资源并校验操作权限。

    不存在、已删除、类型不符、连查看都不满足 → 404（不暴露看不到的资源是否存在）；
    能查看但不满足所需操作 → 403。
    """
    row = (
        await session.execute(
            sa.select(Resource, Org.path)
            .join(Org, Resource.org_id == Org.id)
            .where(Resource.id == resource_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError()
    resource, org_path = row
    if resource.deleted_at is not None or (type is not None and resource.type != type):
        raise NotFoundError()

    is_member = (
        await session.scalar(
            sa.select(ResourceMember.resource_id).where(
                ResourceMember.resource_id == resource.id,
                ResourceMember.user_id == ctx.user_id,
            )
        )
        is not None
    )
    if not _can(ctx, VIEW, resource, org_path, is_member):
        raise NotFoundError()
    if not _can(ctx, action, resource, org_path, is_member):
        raise PermissionDeniedError()
    return resource
