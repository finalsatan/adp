"""个人中心改资料测试。"""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.iam.tables import User
from tests.factories import auth_cookies, make_user


def login_as(client: AsyncClient, user: User) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def test_update_me(client: AsyncClient, session: AsyncSession, org_tree) -> None:
    user = await make_user(session, Role.builder, org_tree.rd, username="meuser")
    login_as(client, user)

    response = await client.patch(
        "/api/v1/me",
        json={"name": "新名字", "email": "new@corp.com", "phone": "13800000000"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "新名字"
    assert body["email"] == "new@corp.com"
    assert body["phone"] == "13800000000"
    # 只读字段不变
    assert body["username"] == "meuser"
    assert body["role"] == "builder"
    assert body["org"] == {"id": org_tree.rd.id, "name": org_tree.rd.name}

    stored = await session.get(User, user.id)
    await session.refresh(stored)
    assert stored.name == "新名字"
    assert stored.email == "new@corp.com"

    logs = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "user.update")))
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].actor_id == user.id
    assert logs[0].target_id == user.id
    assert logs[0].detail == {
        "name": ["meuser", "新名字"],
        "email": [None, "new@corp.com"],
        "phone": [None, "13800000000"],
    }


async def test_update_me_rejects_extra_fields(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    user = await make_user(session, Role.builder, org_tree.rd, username="meuser")
    login_as(client, user)

    for payload in (
        {"username": "other"},
        {"role": "sys_admin"},
        {"org_id": org_tree.group.id},
        {"name": "x", "status": "disabled"},
    ):
        response = await client.patch("/api/v1/me", json=payload)
        assert response.status_code == 422, payload
        assert response.json()["code"] == "VALIDATION_ERROR"
