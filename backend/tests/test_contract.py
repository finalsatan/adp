"""第一期接口契约：路径清单、占位返回、权限映射、结构字段。"""

from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.schemas import Role
from app.iam.permissions import ROLE_PERMISSIONS
from tests.factories import auth_cookies, make_org, make_user
from tests.test_contract_p3 import EXPECTED_OPERATIONS as EXPECTED_P3_OPERATIONS

# 第一期全部“方法 + 路径”，路径参数按 OpenAPI 的写法（不带 int 转换器）。
EXPECTED_OPERATIONS: set[tuple[str, str]] = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/change-password"),
    ("GET", "/api/v1/auth/policy"),
    ("GET", "/api/v1/me"),
    ("PATCH", "/api/v1/me"),
    ("GET", "/api/v1/orgs"),
    ("POST", "/api/v1/orgs"),
    ("PATCH", "/api/v1/orgs/{id}"),
    ("DELETE", "/api/v1/orgs/{id}"),
    ("GET", "/api/v1/orgs/{id}/members"),
    ("GET", "/api/v1/users"),
    ("POST", "/api/v1/users"),
    ("GET", "/api/v1/users/{id}"),
    ("PATCH", "/api/v1/users/{id}"),
    ("POST", "/api/v1/users/{id}:disable"),
    ("POST", "/api/v1/users/{id}:enable"),
    ("POST", "/api/v1/users/{id}:reset-password"),
    ("POST", "/api/v1/users/{id}:unlock"),
    ("POST", "/api/v1/users/{id}:grant-sys-admin"),
    ("POST", "/api/v1/users/{id}:revoke-sys-admin"),
    ("GET", "/api/v1/users:search"),
    ("GET", "/api/v1/resources"),
    ("GET", "/api/v1/resource-types"),
    ("PATCH", "/api/v1/resources/{id}/visibility"),
    ("GET", "/api/v1/resources/{id}/members"),
    ("POST", "/api/v1/resources/{id}/members"),
    ("DELETE", "/api/v1/resources/{id}/members/{user_id}"),
    ("POST", "/api/v1/resources/{id}:disable"),
    ("POST", "/api/v1/resources/{id}:enable"),
    ("DELETE", "/api/v1/resources/{id}"),
    ("GET", "/api/v1/audit-logs"),
    ("GET", "/api/v1/audit-logs/meta"),
    ("GET", "/api/v1/settings"),
    ("PATCH", "/api/v1/settings"),
}

# 第二期接口（P2-01），和第一期一起做全量清单比对。
EXPECTED_P2_OPERATIONS: set[tuple[str, str]] = {
    ("GET", "/api/v1/models"),
    ("POST", "/api/v1/models"),
    ("GET", "/api/v1/models/{id}"),
    ("PATCH", "/api/v1/models/{id}"),
    ("GET", "/api/v1/models/{id}/refs"),
    ("POST", "/api/v1/models/{id}:disable"),
    ("POST", "/api/v1/models/{id}:enable"),
    ("DELETE", "/api/v1/models/{id}"),
    ("POST", "/api/v1/models/{id}:test"),
    ("POST", "/api/v1/models:test"),
    ("POST", "/api/v1/models/{id}/chat"),
    ("GET", "/api/v1/usage/summary"),
    ("GET", "/api/v1/usage/daily"),
    ("GET", "/api/v1/usage/meta"),
}

# 调用每个占位接口用的合法请求（方法、路径、请求体）。
# P1-12 实现资源接口后，第一期不再有占位接口。
PLACEHOLDER_CALLS: list[tuple[str, str, dict[str, Any] | None]] = []


def test_operations_exactly_match_contract(app) -> None:
    paths = app.openapi()["paths"]
    actual: set[tuple[str, str]] = set()
    for path, methods in paths.items():
        if not path.startswith("/api/v1"):
            continue
        for method in methods:
            actual.add((method.upper(), path))
    assert actual == EXPECTED_OPERATIONS | EXPECTED_P2_OPERATIONS | EXPECTED_P3_OPERATIONS


@pytest_asyncio.fixture
async def admin_cookies(session) -> dict[str, str]:
    """占位接口现在都要求登录，用系统管理员访问才能走到占位实现。"""
    user = await make_user(session, role=Role.sys_admin)
    return auth_cookies(user)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    PLACEHOLDER_CALLS,
    ids=[f"{m} {p}" for m, p, _ in PLACEHOLDER_CALLS],
)
async def test_placeholder_returns_not_implemented(
    client: AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    admin_cookies: dict[str, str],
) -> None:
    response = await client.request(method, path, json=body, cookies=admin_cookies)
    assert response.status_code == 501
    assert response.json()["code"] == "NOT_IMPLEMENTED"


def test_role_permissions_match_contract() -> None:
    actual = {role: set(perms) for role, perms in ROLE_PERMISSIONS.items()}
    assert actual == {
        Role.sys_admin: {
            "settings.manage",
            "model.manage",
            "org.manage",
            "user.manage",
            "audit.view",
            "overview.view",
            "usage.view",
            "resource.create",
            "console.use",
        },
        Role.org_admin: {
            "org.manage",
            "user.manage",
            "audit.view",
            "overview.view",
            "usage.view",
            "resource.create",
            "console.use",
        },
        Role.builder: {"resource.create", "console.use"},
        Role.end_user: set(),
    }


def test_schema_field_names(app) -> None:
    schemas = app.openapi()["components"]["schemas"]

    assert set(schemas["MeOut"]["properties"]) == {
        "id",
        "username",
        "name",
        "email",
        "phone",
        "role",
        "org",
        "permissions",
        "must_change_password",
    }
    assert set(schemas["UserOut"]["properties"]) == {
        "id",
        "username",
        "name",
        "email",
        "phone",
        "remark",
        "org",
        "role",
        "status",
        "locked",
        "last_login_at",
        "created_at",
        "created_by",
        "owned_resource_count",
        "manageable",
    }
    assert set(schemas["OrgOut"]["properties"]) == {
        "id",
        "parent_id",
        "path",
        "name",
        "remark",
        "admins",
        "user_count",
        "resource_count",
        "child_count",
        "created_at",
        "editable",
    }
    assert set(schemas["SettingsOut"]["properties"]) == {
        "password",
        "login",
        "session",
        "model_call",
        "upload",
    }
    assert set(schemas["PasswordSettings"]["properties"]) == {"min_length", "min_classes"}
    assert set(schemas["LoginSettings"]["properties"]) == {"lock_threshold", "lock_minutes"}
    assert set(schemas["SessionSettings"]["properties"]) == {"access_minutes", "refresh_days"}
    assert set(schemas["ModelCallSettings"]["properties"]) == {
        "ingest_rate_ratio",
        "model_host_allowlist",
    }


async def test_action_routes_use_int_converter(
    client: AsyncClient, session, admin_cookies: dict[str, str]
) -> None:
    # 停用路由：id 是整数，动作后缀不能被当成 id 的一部分
    org = await make_org(session, "路由组织")
    target = await make_user(session, Role.builder, org=org, username="routeuser")

    # 搜索路由：静态路径优先于 /users/{id}
    searched = await client.get(
        "/api/v1/users:search", params={"q": "route"}, cookies=admin_cookies
    )
    assert searched.status_code == 200
    assert [row["username"] for row in searched.json()] == ["routeuser"]

    # 停用路由：id 是整数，动作后缀不能被当成 id 的一部分
    disabled = await client.post(f"/api/v1/users/{target.id}:disable", cookies=admin_cookies)
    assert disabled.status_code == 200

    # 非整数的 id 不匹配任何路由
    missing = await client.get("/api/v1/users/abc")
    assert missing.status_code == 404
