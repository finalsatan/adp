"""第一期接口契约：路径清单、占位返回、权限映射、结构字段。"""

from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.schemas import Role
from app.iam.permissions import ROLE_PERMISSIONS
from tests.factories import auth_cookies, make_user

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

# 调用每个占位接口用的合法请求（方法、路径、请求体）。
PLACEHOLDER_CALLS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/v1/auth/login", {"username": "someone", "password": "Secret123!"}),
    ("POST", "/api/v1/auth/refresh", None),
    ("POST", "/api/v1/auth/logout", None),
    ("POST", "/api/v1/auth/change-password", {"new_password": "Secret123!"}),
    ("GET", "/api/v1/auth/policy", None),
    ("PATCH", "/api/v1/me", {"name": "张三"}),
    ("GET", "/api/v1/orgs", None),
    ("POST", "/api/v1/orgs", {"name": "研发中心"}),
    ("PATCH", "/api/v1/orgs/1", {"name": "研发中心"}),
    ("DELETE", "/api/v1/orgs/1", None),
    ("GET", "/api/v1/orgs/1/members", None),
    ("GET", "/api/v1/users", None),
    (
        "POST",
        "/api/v1/users",
        {
            "username": "zhangsan",
            "password": "Secret123!",
            "name": "张三",
            "org_id": 1,
            "role": "builder",
        },
    ),
    ("GET", "/api/v1/users/1", None),
    ("PATCH", "/api/v1/users/1", {"name": "张三"}),
    ("POST", "/api/v1/users/1:disable", None),
    ("POST", "/api/v1/users/1:enable", None),
    ("POST", "/api/v1/users/1:reset-password", {"new_password": "Secret123!"}),
    ("POST", "/api/v1/users/1:unlock", None),
    ("POST", "/api/v1/users/1:grant-sys-admin", None),
    ("POST", "/api/v1/users/1:revoke-sys-admin", {"org_id": 1, "role": "builder"}),
    ("GET", "/api/v1/users:search?q=a", None),
    ("GET", "/api/v1/resources", None),
    ("GET", "/api/v1/resource-types", None),
    ("PATCH", "/api/v1/resources/1/visibility", {"visibility": "org"}),
    ("GET", "/api/v1/resources/1/members", None),
    ("POST", "/api/v1/resources/1/members", {"user_id": 2}),
    ("DELETE", "/api/v1/resources/1/members/2", None),
    ("POST", "/api/v1/resources/1:disable", None),
    ("POST", "/api/v1/resources/1:enable", None),
    ("DELETE", "/api/v1/resources/1", None),
    ("GET", "/api/v1/audit-logs", None),
    ("GET", "/api/v1/audit-logs/meta", None),
    ("GET", "/api/v1/settings", None),
    ("PATCH", "/api/v1/settings", {"password": {"min_length": 10}}),
]


def test_operations_exactly_match_contract(app) -> None:
    paths = app.openapi()["paths"]
    actual: set[tuple[str, str]] = set()
    for path, methods in paths.items():
        if not path.startswith("/api/v1"):
            continue
        for method in methods:
            actual.add((method.upper(), path))
    assert actual == EXPECTED_OPERATIONS


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
    }
    assert set(schemas["PasswordSettings"]["properties"]) == {"min_length", "min_classes"}
    assert set(schemas["LoginSettings"]["properties"]) == {"lock_threshold", "lock_minutes"}
    assert set(schemas["SessionSettings"]["properties"]) == {"access_minutes", "refresh_days"}
    assert set(schemas["ModelCallSettings"]["properties"]) == {
        "ingest_rate_ratio",
        "model_host_allowlist",
    }


async def test_action_routes_use_int_converter(
    client: AsyncClient, admin_cookies: dict[str, str]
) -> None:
    # 停用路由：id 是整数，动作后缀不能被当成 id 的一部分
    disabled = await client.post("/api/v1/users/5:disable", cookies=admin_cookies)
    assert disabled.status_code == 501

    # 搜索路由：静态路径优先于 /users/{id}
    searched = await client.get("/api/v1/users:search", params={"q": "a"}, cookies=admin_cookies)
    assert searched.status_code == 501

    # 非整数的 id 不匹配任何路由
    missing = await client.get("/api/v1/users/abc")
    assert missing.status_code == 404
