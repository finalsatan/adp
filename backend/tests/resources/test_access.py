"""资源权限判断测试（技术方案 13.2“资源权限”逐项）。"""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.schemas import Role
from app.iam.context import Ctx
from app.iam.permissions import ROLE_PERMISSIONS
from app.iam.tables import Org, User
from app.resources import access
from app.resources.tables import Resource, ResourceMember
from tests.factories import make_user


def make_ctx(user: User, org: Org | None) -> Ctx:
    role = Role(user.role)
    return Ctx(
        user_id=user.id,
        username=user.username,
        name=user.name,
        role=role,
        status=user.status,
        org_id=org.id if org is not None else None,
        org_path=org.path if org is not None else None,
        org_name=org.name if org is not None else None,
        perms=ROLE_PERMISSIONS.get(role, frozenset()),
        session_id="test-session",
        pwc=False,
        ip=None,
        request_id=None,
    )


async def ctx_of(session: AsyncSession, user: User) -> Ctx:
    org = await session.get(Org, user.org_id) if user.org_id is not None else None
    return make_ctx(user, org)


async def make_resource(
    session: AsyncSession,
    org: Org,
    owner: User,
    visibility: str = "org",
    type: str = "test_res",
) -> Resource:
    resource = Resource(
        type=type,
        name=f"res-{uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility=visibility,
        status="enabled",
    )
    session.add(resource)
    await session.commit()
    await session.refresh(resource)
    return resource


async def add_member(session: AsyncSession, resource: Resource, user: User, added_by: User) -> None:
    session.add(ResourceMember(resource_id=resource.id, user_id=user.id, added_by=added_by.id))
    await session.commit()


async def clause_allows(session: AsyncSession, ctx: Ctx, resource: Resource, action: str) -> bool:
    stmt = (
        sa.select(Resource.id)
        .join(Org, Resource.org_id == Org.id)
        .where(Resource.id == resource.id, access.access_clause(ctx, action))
    )
    return await session.scalar(stmt) is not None


async def get_allows(session: AsyncSession, ctx: Ctx, resource: Resource, action: str) -> bool:
    try:
        await access.get_for(session, ctx, resource.id, action)
    except (NotFoundError, PermissionDeniedError):
        return False
    return True


async def test_org_visibility_includes_descendants(
    session: AsyncSession, org_tree, test_res_type
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd, username="org_owner")
    resource = await make_resource(session, org_tree.rd, owner, visibility="org")

    for org in (org_tree.platform, org_tree.algo):
        user = await make_user(session, Role.builder, org, username=f"u_{org.name}")
        await access.get_for(session, await ctx_of(session, user), resource.id, "view")

    for org in (org_tree.product, org_tree.group):
        user = await make_user(session, Role.builder, org, username=f"u_{org.name}")
        with pytest.raises(NotFoundError):
            await access.get_for(session, await ctx_of(session, user), resource.id, "view")


async def test_private_visibility_limits_view(
    session: AsyncSession, org_tree, test_res_type
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd, username="private_owner")
    resource = await make_resource(session, org_tree.rd, owner, visibility="private")
    same_org_outsider = await make_user(session, Role.builder, org_tree.rd, username="rd_other")
    member = await make_user(session, Role.builder, org_tree.algo, username="algo_member")
    rd_admin = await make_user(session, Role.org_admin, org_tree.rd, username="rd_admin")
    sys_admin = await make_user(session, Role.sys_admin, username="sys_admin")
    await add_member(session, resource, member, owner)

    for user in (owner, member, rd_admin, sys_admin):
        await access.get_for(session, await ctx_of(session, user), resource.id, "view")

    with pytest.raises(NotFoundError):
        await access.get_for(session, await ctx_of(session, same_org_outsider), resource.id, "view")


async def test_member_can_view_edit_not_manage(
    session: AsyncSession, org_tree, test_res_type
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd, username="owner")
    member = await make_user(session, Role.builder, org_tree.algo, username="member")
    resource = await make_resource(session, org_tree.rd, owner, visibility="private")
    await add_member(session, resource, member, owner)

    ctx = await ctx_of(session, member)
    await access.get_for(session, ctx, resource.id, "view")
    await access.get_for(session, ctx, resource.id, "edit")
    with pytest.raises(PermissionDeniedError):
        await access.get_for(session, ctx, resource.id, "manage")


async def test_owner_moved_loses_owner_permission(
    session: AsyncSession, org_tree, test_res_type
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd, username="mover")
    resource = await make_resource(session, org_tree.rd, owner, visibility="private")

    await access.get_for(session, await ctx_of(session, owner), resource.id, "manage")

    # 调到其他组织后不再是有效创建人，私有资源连查看都不行
    owner.org_id = org_tree.product.id
    await session.commit()
    with pytest.raises(NotFoundError):
        await access.get_for(session, await ctx_of(session, owner), resource.id, "view")

    # 再被加为协作者后能编辑，但仍不能管理
    await add_member(session, resource, owner, owner)
    ctx = await ctx_of(session, owner)
    await access.get_for(session, ctx, resource.id, "edit")
    with pytest.raises(PermissionDeniedError):
        await access.get_for(session, ctx, resource.id, "manage")


async def test_scoped_admin_can_manage_only_in_scope(
    session: AsyncSession, org_tree, test_res_type
) -> None:
    rd_admin = await make_user(session, Role.org_admin, org_tree.rd, username="rd_admin")
    owner = await make_user(session, Role.builder, org_tree.rd, username="owner")
    in_rd = await make_resource(session, org_tree.rd, owner, visibility="private")
    in_platform = await make_resource(session, org_tree.platform, owner, visibility="private")
    out_product = await make_resource(session, org_tree.product, owner, visibility="platform")
    out_private = await make_resource(session, org_tree.product, owner, visibility="private")

    ctx = await ctx_of(session, rd_admin)
    await access.get_for(session, ctx, in_rd.id, "manage")
    await access.get_for(session, ctx, in_platform.id, "manage")
    # 范围外的全平台资源能查看，但不能管理
    await access.get_for(session, ctx, out_product.id, "view")
    with pytest.raises(PermissionDeniedError):
        await access.get_for(session, ctx, out_product.id, "manage")
    # 范围外的私有资源看不到
    with pytest.raises(NotFoundError):
        await access.get_for(session, ctx, out_private.id, "view")


async def test_access_clause_matches_get_for(
    session: AsyncSession, org_tree, test_res_type
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd, username="owner")
    rd_other = await make_user(session, Role.builder, org_tree.rd, username="rd_other")
    member = await make_user(session, Role.builder, org_tree.algo, username="member")
    rd_admin = await make_user(session, Role.org_admin, org_tree.rd, username="rd_admin")
    product_admin = await make_user(session, Role.org_admin, org_tree.product, username="p_admin")
    product_user = await make_user(session, Role.builder, org_tree.product, username="p_user")
    sys_admin = await make_user(session, Role.sys_admin, username="sys_admin")

    resources = [
        await make_resource(session, org_tree.rd, owner, visibility="private"),
        await make_resource(session, org_tree.rd, owner, visibility="org"),
        await make_resource(session, org_tree.platform, owner, visibility="private"),
        await make_resource(session, org_tree.product, product_user, visibility="platform"),
    ]
    await add_member(session, resources[0], member, owner)

    contexts = [
        await ctx_of(session, user)
        for user in (owner, rd_other, member, rd_admin, product_admin, product_user, sys_admin)
    ]

    for ctx in contexts:
        for resource in resources:
            for action in ("view", "edit", "manage"):
                by_clause = await clause_allows(session, ctx, resource, action)
                by_get = await get_allows(session, ctx, resource, action)
                assert by_clause == by_get, (ctx.username, resource.id, action)
