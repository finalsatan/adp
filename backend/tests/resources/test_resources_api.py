"""资源总览与通用资源接口测试。"""

import json
from uuid import uuid4

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tables import AuditLog
from app.core.errors import NameConflictError, OrgRequiredError
from app.core.schemas import Role, Visibility
from app.iam.context import Ctx
from app.iam.permissions import ROLE_PERMISSIONS
from app.iam.tables import Org, User
from app.resources import service
from app.resources.registry import (
    RefBlocker,
    ResourceType,
    register_ref_checker,
    register_resource_type,
    unregister_ref_checker,
    unregister_resource_type,
)
from app.resources.tables import Resource, ResourceRef
from tests.factories import auth_cookies, make_user


def login_as(client: AsyncClient, user: User) -> None:
    """在 client 实例上设 Cookie，供后续请求使用。"""
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def ctx_of(session: AsyncSession, user: User) -> Ctx:
    org = await session.get(Org, user.org_id) if user.org_id is not None else None
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


async def test_overview_scope_and_builder_forbidden(
    client: AsyncClient, session: AsyncSession, org_tree, test_res_type
) -> None:
    sys_admin = await make_user(session, Role.sys_admin, username="sys_admin")
    rd_admin = await make_user(session, Role.org_admin, org_tree.rd, username="rd_admin")
    builder = await make_user(session, Role.builder, org_tree.rd, username="rd_builder")
    owner_rd = await make_user(session, Role.builder, org_tree.rd, username="owner_rd")
    owner_product = await make_user(session, Role.builder, org_tree.product, username="owner_p")

    in_rd = await make_resource(session, org_tree.rd, owner_rd)
    in_platform = await make_resource(session, org_tree.platform, owner_rd)
    await make_resource(session, org_tree.product, owner_product)

    login_as(client, sys_admin)
    response = await client.get("/api/v1/resources")
    assert response.status_code == 200
    assert response.json()["total"] == 3

    login_as(client, rd_admin)
    response = await client.get("/api/v1/resources")
    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {in_rd.id, in_platform.id}
    assert response.json()["total"] == 2

    login_as(client, builder)
    response = await client.get("/api/v1/resources")
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"

    # 关键字筛选转义通配符：输入 % 不会匹配全部
    login_as(client, sys_admin)
    filtered = await client.get("/api/v1/resources", params={"keyword": "%"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 0


async def test_empty_registry_returns_empty(client: AsyncClient, session: AsyncSession) -> None:
    sys_admin = await make_user(session, Role.sys_admin, username="sys_admin")
    login_as(client, sys_admin)

    types = await client.get("/api/v1/resource-types")
    assert types.status_code == 200
    assert types.json() == []

    resources = await client.get("/api/v1/resources")
    assert resources.status_code == 200
    assert resources.json() == {"items": [], "total": 0}


async def test_resource_types_when_registered(
    client: AsyncClient, session: AsyncSession, test_res_type
) -> None:
    sys_admin = await make_user(session, Role.sys_admin, username="sys_admin")
    login_as(client, sys_admin)

    response = await client.get("/api/v1/resource-types")
    assert response.status_code == 200
    assert response.json() == [{"code": "test_res", "label": "测试资源", "fixed_visibility": None}]


async def test_create_resource_service(session: AsyncSession, org_tree, test_res_type) -> None:
    sys_admin = await make_user(session, Role.sys_admin, username="sys_admin")
    sys_ctx = await ctx_of(session, sys_admin)

    with pytest.raises(OrgRequiredError) as missing:
        await service.create_resource(session, sys_ctx, "test_res", "资源A", Visibility.org)
    assert missing.value.status == 422
    assert missing.value.code == "ORG_REQUIRED"

    created = await service.create_resource(
        session, sys_ctx, "test_res", "资源A", Visibility.org, org_id=org_tree.rd.id
    )
    assert created.org_id == org_tree.rd.id

    with pytest.raises(NameConflictError) as conflict:
        await service.create_resource(
            session, sys_ctx, "test_res", "资源A", Visibility.org, org_id=org_tree.rd.id
        )
    assert conflict.value.status == 409
    assert conflict.value.code == "NAME_CONFLICT"

    # 不同组织同名可以
    other = await service.create_resource(
        session, sys_ctx, "test_res", "资源A", Visibility.org, org_id=org_tree.platform.id
    )
    assert other.id != created.id

    # 非系统管理员忽略请求里的 org_id，取本人组织
    builder = await make_user(session, Role.builder, org_tree.rd, username="builder")
    builder_ctx = await ctx_of(session, builder)
    mine = await service.create_resource(
        session, builder_ctx, "test_res", "资源B", Visibility.org, org_id=org_tree.product.id
    )
    assert mine.org_id == org_tree.rd.id


async def test_fixed_visibility_rejects_change(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    fixed = ResourceType(code="fixed_res", label="固定资源", fixed_visibility="platform")
    register_resource_type(fixed)
    try:
        owner = await make_user(session, Role.builder, org_tree.rd, username="fixed_owner")
        resource = await make_resource(
            session, org_tree.rd, owner, visibility="platform", type="fixed_res"
        )
        login_as(client, owner)
        response = await client.patch(
            f"/api/v1/resources/{resource.id}/visibility", json={"visibility": "private"}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "VISIBILITY_FIXED"
    finally:
        unregister_resource_type("fixed_res")


async def test_shrink_visibility_and_remove_member_run_ref_checks(
    client: AsyncClient, session: AsyncSession, org_tree, test_res_type
) -> None:
    async def fake_checker(session, resource, new_visibility, removed_user_id):
        return [RefBlocker(id=999, type="app", name="引用应用")]

    register_resource_type(ResourceType(code="check_res", label="检查资源", fixed_visibility=None))
    register_resource_type(ResourceType(code="expand_res", label="扩展资源"))

    register_ref_checker(fake_checker)
    try:
        owner = await make_user(session, Role.builder, org_tree.rd, username="check_owner")
        member = await make_user(session, Role.builder, org_tree.algo, username="check_member")
        resource = await make_resource(
            session, org_tree.rd, owner, visibility="platform", type="check_res"
        )
        expandable = await make_resource(
            session, org_tree.rd, owner, visibility="org", type="expand_res"
        )
        login_as(client, owner)

        shrunk = await client.patch(
            f"/api/v1/resources/{resource.id}/visibility", json={"visibility": "org"}
        )
        assert shrunk.status_code == 409
        assert shrunk.json()["code"] == "RESOURCE_IN_USE"
        assert shrunk.json()["detail"]["refs"] == [{"id": 999, "type": "app", "name": "引用应用"}]

        # 扩大范围不做引用检查
        expanded = await client.patch(
            f"/api/v1/resources/{expandable.id}/visibility", json={"visibility": "platform"}
        )
        assert expanded.status_code == 200

        added = await client.post(
            f"/api/v1/resources/{resource.id}/members", json={"user_id": member.id}
        )
        assert added.status_code == 201
        removed = await client.delete(f"/api/v1/resources/{resource.id}/members/{member.id}")
        assert removed.status_code == 409
        assert removed.json()["detail"]["refs"][0]["id"] == 999
    finally:
        unregister_ref_checker(fake_checker)
        unregister_resource_type("check_res")
        unregister_resource_type("expand_res")


async def test_delete_blocked_by_resource_ref(
    client: AsyncClient, session: AsyncSession, org_tree, test_res_type
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd, username="delete_owner")
    target = await make_resource(session, org_tree.rd, owner, visibility="org")
    referencing = await make_resource(session, org_tree.rd, owner, visibility="org")
    session.add(
        ResourceRef(target_type="resource", target_id=target.id, ref_resource_id=referencing.id)
    )
    await session.commit()

    login_as(client, owner)
    blocked = await client.delete(f"/api/v1/resources/{target.id}")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "RESOURCE_IN_USE"
    assert blocked.json()["detail"]["refs"] == [
        {"id": referencing.id, "type": "test_res", "name": referencing.name}
    ]

    # 模型引用不阻止资源删除
    only_model_ref = await make_resource(session, org_tree.rd, owner, visibility="org")
    session.add(ResourceRef(target_type="model", target_id=1, ref_resource_id=only_model_ref.id))
    await session.commit()
    deleted = await client.delete(f"/api/v1/resources/{only_model_ref.id}")
    assert deleted.status_code == 204


async def test_members_flow(
    client: AsyncClient, session: AsyncSession, org_tree, test_res_type
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd, username="member_owner")
    member = await make_user(session, Role.builder, org_tree.algo, username="member_user")
    outsider = await make_user(session, Role.builder, org_tree.rd, username="member_outsider")
    resource = await make_resource(session, org_tree.rd, owner, visibility="private")

    login_as(client, owner)
    added = await client.post(
        f"/api/v1/resources/{resource.id}/members", json={"user_id": member.id}
    )
    assert added.status_code == 201
    assert added.json()["user"]["id"] == member.id
    assert added.json()["added_by"]["username"] == owner.username

    duplicate = await client.post(
        f"/api/v1/resources/{resource.id}/members", json={"user_id": member.id}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "ALREADY_MEMBER"

    missing_user = await client.post(
        f"/api/v1/resources/{resource.id}/members", json={"user_id": 999999}
    )
    assert missing_user.status_code == 404

    listing = await client.get(f"/api/v1/resources/{resource.id}/members")
    assert listing.status_code == 200
    assert [row["user"]["id"] for row in listing.json()] == [member.id]

    # 看不到资源的同组织用户取成员列表 404
    login_as(client, outsider)
    assert (await client.get(f"/api/v1/resources/{resource.id}/members")).status_code == 404

    # 协作者能查看，不能增删协作者
    login_as(client, member)
    assert (await client.get(f"/api/v1/resources/{resource.id}/members")).status_code == 200
    denied = await client.post(
        f"/api/v1/resources/{resource.id}/members", json={"user_id": outsider.id}
    )
    assert denied.status_code == 403


async def test_audit_meta_and_label_use_registry(
    client: AsyncClient, session: AsyncSession, org_tree, test_res_type
) -> None:
    sys_admin = await make_user(session, Role.sys_admin, username="sys_admin")
    owner = await make_user(session, Role.builder, org_tree.rd, username="label_owner")
    resource = await make_resource(session, org_tree.rd, owner, visibility="org")

    login_as(client, owner)
    changed = await client.patch(
        f"/api/v1/resources/{resource.id}/visibility", json={"visibility": "platform"}
    )
    assert changed.status_code == 200

    login_as(client, sys_admin)
    meta = await client.get("/api/v1/audit-logs/meta")
    assert {"code": "test_res", "label": "测试资源"} in meta.json()["target_types"]

    logs = await client.get("/api/v1/audit-logs", params={"target_type": "test_res"})
    row = next(item for item in logs.json()["items"] if item["target_id"] == resource.id)
    assert row["target_type_label"] == "测试资源"


async def test_write_actions_are_audited(
    client: AsyncClient, session: AsyncSession, org_tree, test_res_type
) -> None:
    owner = await make_user(session, Role.builder, org_tree.rd, username="audit_owner")
    member = await make_user(session, Role.builder, org_tree.algo, username="audit_member")
    resource = await make_resource(session, org_tree.rd, owner, visibility="private")

    login_as(client, owner)
    assert (
        await client.post(f"/api/v1/resources/{resource.id}/members", json={"user_id": member.id})
    ).status_code == 201
    assert (
        await client.patch(
            f"/api/v1/resources/{resource.id}/visibility", json={"visibility": "org"}
        )
    ).status_code == 200
    assert (await client.post(f"/api/v1/resources/{resource.id}:disable")).status_code == 200
    assert (await client.post(f"/api/v1/resources/{resource.id}:enable")).status_code == 200
    assert (
        await client.delete(f"/api/v1/resources/{resource.id}/members/{member.id}")
    ).status_code == 204
    assert (await client.delete(f"/api/v1/resources/{resource.id}")).status_code == 204

    logs = (
        (
            await session.execute(
                sa.select(AuditLog)
                .where(AuditLog.target_type == "test_res", AuditLog.target_id == resource.id)
                .order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )
    actions = {log.action for log in logs}
    assert {
        "resource.add_member",
        "resource.change_visibility",
        "resource.disable",
        "resource.enable",
        "resource.remove_member",
        "resource.delete",
    } <= actions

    visibility_log = next(log for log in logs if log.action == "resource.change_visibility")
    assert visibility_log.detail == {"from": "private", "to": "org"}
    add_log = next(log for log in logs if log.action == "resource.add_member")
    assert add_log.detail == {"username": member.username}
    assert add_log.target_desc == resource.name
    assert add_log.target_org_id == org_tree.rd.id
    assert "audit_member" in json.dumps([log.detail for log in logs])
