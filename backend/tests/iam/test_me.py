"""GET /me 测试。"""

import pytest
from httpx import AsyncClient

from app.core.schemas import Role
from app.iam.permissions import ROLE_PERMISSIONS
from tests.factories import auth_cookies, make_org, make_user


@pytest.mark.parametrize("role", list(Role))
async def test_me(client: AsyncClient, session, role: Role) -> None:
    org = None if role == Role.sys_admin else await make_org(session, f"org-{role.value}")
    user = await make_user(
        session,
        role=role,
        org=org,
        username=f"user-{role.value}",
        must_change_password=False,
    )

    response = await client.get("/api/v1/me", cookies=auth_cookies(user))
    assert response.status_code == 200
    data = response.json()

    assert data["id"] == user.id
    assert data["username"] == user.username
    assert data["name"] == user.name
    assert data["role"] == role.value
    assert data["permissions"] == sorted(ROLE_PERMISSIONS[role])
    assert data["must_change_password"] is False
    if org is None:
        assert data["org"] is None
    else:
        assert data["org"] == {"id": org.id, "name": org.name}
