"""系统管理员任命/撤销测试，以及停用最后一个系统管理员的保护。"""

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import LastSysAdminError
from app.core.schemas import Role
from app.iam import users_service
from app.iam.context import Ctx
from app.iam.permissions import ROLE_PERMISSIONS
from app.iam.tables import User
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_user


def login_as(client: AsyncClient, user: User) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def test_grant_sys_admin_keeps_resources(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    sys_admin = await make_user(session, Role.sys_admin, username="root")
    target = await make_user(session, Role.builder, org_tree.rd, username="candidate")
    resource = Resource(
        type="kb",
        name="candidate-kb",
        org_id=org_tree.rd.id,
        owner_id=target.id,
        visibility="org",
        status="enabled",
    )
    session.add(resource)
    await session.commit()

    login_as(client, sys_admin)
    response = await client.post(f"/api/v1/users/{target.id}:grant-sys-admin")
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "sys_admin"
    assert body["org"] is None

    # 资源仍留在原组织
    stored = await session.get(Resource, resource.id)
    assert stored.org_id == org_tree.rd.id
    assert stored.owner_id == target.id


async def test_revoke_last_sys_admin_409(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    sys_admin = await make_user(session, Role.sys_admin, username="root")
    login_as(client, sys_admin)

    response = await client.post(
        f"/api/v1/users/{sys_admin.id}:revoke-sys-admin",
        json={"org_id": org_tree.rd.id, "role": "builder"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "LAST_SYS_ADMIN"


async def test_disable_last_sys_admin_409(session: AsyncSession, redis: Redis, org_tree) -> None:
    """接口上系统管理员不能停用自己（403），这里直接验证服务层的兜底检查。"""
    operator = await make_user(session, Role.sys_admin, username="root", status="disabled")
    last = await make_user(session, Role.sys_admin, username="last")
    ctx = Ctx(
        user_id=operator.id,
        username=operator.username,
        name=operator.name,
        role=Role.sys_admin,
        status="active",
        org_id=None,
        org_path=None,
        org_name=None,
        perms=ROLE_PERMISSIONS[Role.sys_admin],
        session_id="test",
        pwc=False,
        ip=None,
        request_id=None,
    )

    with pytest.raises(LastSysAdminError):
        await users_service.disable_user(session, redis, ctx, last.id)


async def test_revoke_with_two_sys_admins(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    first = await make_user(session, Role.sys_admin, username="root")
    second = await make_user(session, Role.sys_admin, username="second")

    login_as(client, first)
    response = await client.post(
        f"/api/v1/users/{second.id}:revoke-sys-admin",
        json={"org_id": org_tree.platform.id, "role": "builder"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "builder"
    assert body["org"] == {"id": org_tree.platform.id, "name": org_tree.platform.name}


async def test_revoke_self_with_another_sys_admin(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    first = await make_user(session, Role.sys_admin, username="root")
    await make_user(session, Role.sys_admin, username="second")

    login_as(client, first)
    response = await client.post(
        f"/api/v1/users/{first.id}:revoke-sys-admin",
        json={"org_id": org_tree.rd.id, "role": "org_admin"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "org_admin"


async def test_grant_requires_active_non_sysadmin(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    sys_admin = await make_user(session, Role.sys_admin, username="root")
    disabled = await make_user(
        session, Role.builder, org_tree.rd, username="disabled_user", status="disabled"
    )
    another = await make_user(session, Role.sys_admin, username="second")

    login_as(client, sys_admin)
    assert (await client.post(f"/api/v1/users/{disabled.id}:grant-sys-admin")).status_code == 422
    assert (await client.post(f"/api/v1/users/{another.id}:grant-sys-admin")).status_code == 422


async def test_patch_sys_admin_rejects_role_and_org(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    first = await make_user(session, Role.sys_admin, username="root")
    second = await make_user(session, Role.sys_admin, username="second")

    login_as(client, first)
    assert (
        await client.patch(f"/api/v1/users/{second.id}", json={"role": "builder"})
    ).status_code == 422
    assert (
        await client.patch(f"/api/v1/users/{second.id}", json={"org_id": org_tree.rd.id})
    ).status_code == 422
    # 资料字段仍可改
    assert (
        await client.patch(f"/api/v1/users/{second.id}", json={"name": "改名"})
    ).status_code == 200


async def test_org_admin_cannot_grant_or_revoke(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    rd_admin = await make_user(session, Role.org_admin, org_tree.rd, username="rdadmin")
    target = await make_user(session, Role.builder, org_tree.platform, username="candidate")

    login_as(client, rd_admin)
    assert (await client.post(f"/api/v1/users/{target.id}:grant-sys-admin")).status_code == 403
    assert (
        await client.post(
            f"/api/v1/users/{target.id}:revoke-sys-admin",
            json={"org_id": org_tree.rd.id, "role": "builder"},
        )
    ).status_code == 403
