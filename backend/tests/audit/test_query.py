"""审计查询测试：范围、筛选、标签。"""

from datetime import UTC, datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.main import create_app
from tests.factories import auth_cookies, make_user


@pytest_asyncio.fixture(scope="session")
async def query_app(settings):
    application = create_app(settings)
    yield application
    await application.state.redis.aclose()
    await application.state.engine.dispose()


@pytest_asyncio.fixture
async def query_client(query_app) -> AsyncClient:
    transport = ASGITransport(app=query_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _login(client: AsyncClient, user) -> None:
    client.cookies.set("adp_at", auth_cookies(user)["adp_at"])


async def _add(
    session,
    *,
    action: str = "user.create",
    actor_name: str = "zhangsan",
    actor_org=None,
    target_type: str = "user",
    target_desc: str = "用户 x",
    target_org=None,
    result: str = "success",
    at: datetime | None = None,
) -> AuditLog:
    row = AuditLog(
        actor_id=1,
        actor_name=actor_name,
        actor_org_id=actor_org.id if actor_org is not None else None,
        actor_org_name=actor_org.name if actor_org is not None else None,
        action=action,
        target_type=target_type,
        target_id=1,
        target_desc=target_desc,
        target_org_id=target_org.id if target_org is not None else None,
        target_org_name=target_org.name if target_org is not None else None,
        result=result,
        ip="1.2.3.4",
    )
    if at is not None:
        row.at = at
    session.add(row)
    await session.commit()
    return row


async def _ids(client: AsyncClient, **params) -> set[int]:
    response = await client.get("/api/v1/audit-logs", params={"page_size": 100, **params})
    assert response.status_code == 200
    return {item["id"] for item in response.json()["items"]}


async def test_org_admin_scope(query_client: AsyncClient, session, org_tree) -> None:
    admin = await make_user(session, role=Role.org_admin, org=org_tree.rd, username="rdadmin")
    r_rd = await _add(session, actor_org=org_tree.rd, target_org=org_tree.rd)
    r_child = await _add(session, actor_org=org_tree.platform, target_org=org_tree.rd)
    # 其他组织的协作者改了本组织的资源，本组织管理员也能看到
    r_other_actor = await _add(session, actor_org=org_tree.product, target_org=org_tree.rd)
    r_other_target = await _add(session, actor_org=org_tree.rd, target_org=org_tree.product)
    await _add(session, actor_org=org_tree.product, target_org=org_tree.product)
    await _add(session, actor_org=None, target_org=org_tree.product)

    _login(query_client, admin)
    response = await query_client.get("/api/v1/audit-logs", params={"page_size": 100})
    assert response.status_code == 200
    assert response.json()["total"] == 4
    assert {item["id"] for item in response.json()["items"]} == {
        r_rd.id,
        r_child.id,
        r_other_actor.id,
        r_other_target.id,
    }


async def test_sys_admin_sees_all(query_client: AsyncClient, session, org_tree) -> None:
    admin = await make_user(session, role=Role.sys_admin, username="root")
    await _add(session, actor_org=org_tree.product, target_org=org_tree.product)
    await _add(session, actor_org=org_tree.rd, target_org=org_tree.rd)

    _login(query_client, admin)
    response = await query_client.get("/api/v1/audit-logs", params={"page_size": 100})
    assert response.status_code == 200
    assert response.json()["total"] == 2


async def test_builder_forbidden(query_client: AsyncClient, session, org_tree) -> None:
    builder = await make_user(session, role=Role.builder, org=org_tree.rd, username="b")
    _login(query_client, builder)
    response = await query_client.get("/api/v1/audit-logs")
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


async def test_filters(query_client: AsyncClient, session) -> None:
    admin = await make_user(session, role=Role.sys_admin, username="root")
    r1 = await _add(
        session,
        action="user.create",
        actor_name="zhangsan",
        target_type="user",
        target_desc="用户 chenjiu",
        result="success",
        at=datetime(2024, 1, 1, 10, tzinfo=UTC),
    )
    r2 = await _add(
        session,
        action="org.update",
        actor_name="lisi",
        target_type="org",
        target_desc="组织 算法组",
        result="failure",
        at=datetime(2024, 1, 2, 10, tzinfo=UTC),
    )
    r3 = await _add(
        session,
        action="user.disable",
        actor_name="wangwu",
        target_type="user",
        target_desc="用户 zhaoliu",
        result="success",
        at=datetime(2024, 1, 3, 10, tzinfo=UTC),
    )

    _login(query_client, admin)
    assert await _ids(query_client, action="user.create") == {r1.id}
    assert await _ids(query_client, actor="zhang") == {r1.id}
    assert await _ids(query_client, target_type="org") == {r2.id}
    assert await _ids(query_client, target="chenjiu") == {r1.id}
    assert await _ids(query_client, result="failure") == {r2.id}
    assert await _ids(
        query_client,
        **{
            "from": "2024-01-02T00:00:00+00:00",
            "to": "2024-01-02T23:59:59+00:00",
        },
    ) == {r2.id}

    response = await query_client.get(
        "/api/v1/audit-logs", params={"action": "user.create", "page_size": 100}
    )
    item = response.json()["items"][0]
    assert item["action_label"] == "创建用户"
    assert item["target_type_label"] == "用户"
    assert item["id"] == r1.id
    assert await _ids(query_client, action="user.disable") == {r3.id}


async def test_order_desc(query_client: AsyncClient, session) -> None:
    admin = await make_user(session, role=Role.sys_admin, username="root")
    r1 = await _add(session, at=datetime(2024, 1, 1, tzinfo=UTC))
    r3 = await _add(session, at=datetime(2024, 1, 3, tzinfo=UTC))
    r2 = await _add(session, at=datetime(2024, 1, 2, tzinfo=UTC))

    _login(query_client, admin)
    response = await query_client.get("/api/v1/audit-logs", params={"page_size": 100})
    assert [item["id"] for item in response.json()["items"]] == [r3.id, r2.id, r1.id]


async def test_org_filter_within_scope(query_client: AsyncClient, session, org_tree) -> None:
    admin = await make_user(session, role=Role.org_admin, org=org_tree.rd, username="rdadmin")
    r_rd = await _add(session, actor_org=org_tree.rd, target_org=org_tree.rd)
    r_platform = await _add(session, actor_org=org_tree.platform, target_org=org_tree.rd)
    await _add(session, actor_org=org_tree.product, target_org=org_tree.product)

    _login(query_client, admin)
    assert await _ids(query_client, org_id=org_tree.rd.id) == {r_rd.id, r_platform.id}
    # 范围外的组织：子树与管理范围取交集后为空
    assert await _ids(query_client, org_id=org_tree.product.id) == set()
    # 不存在的组织返回空
    assert await _ids(query_client, org_id=999999) == set()


async def test_meta(query_client: AsyncClient, session) -> None:
    admin = await make_user(session, role=Role.sys_admin, username="root")
    _login(query_client, admin)

    response = await query_client.get("/api/v1/audit-logs/meta")
    assert response.status_code == 200
    data = response.json()
    actions = {item["code"]: item["label"] for item in data["actions"]}
    assert actions["auth.login"] == "登录"
    assert actions["user.create"] == "创建用户"
    assert actions["settings.update"] == "修改系统设置"
    target_types = {item["code"]: item["label"] for item in data["target_types"]}
    assert target_types == {"user": "用户", "org": "组织", "settings": "系统设置"}


async def test_unknown_action_and_type_show_code(query_client: AsyncClient, session) -> None:
    admin = await make_user(session, role=Role.sys_admin, username="root")
    await _add(session, action="future.action", target_type="kb")
    _login(query_client, admin)

    response = await query_client.get("/api/v1/audit-logs")
    item = response.json()["items"][0]
    assert item["action_label"] == "future.action"
    assert item["target_type_label"] == "kb"
