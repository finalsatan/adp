"""第二期接口契约：路径清单、占位返回、SSE 结构、字段名与密钥不泄露。"""

from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.schemas import Role
from tests.factories import auth_cookies, make_user

# 第二期全部“方法 + 路径”，路径参数按 OpenAPI 的写法（不带 int 转换器）。
EXPECTED_OPERATIONS: set[tuple[str, str]] = {
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

_CHAT_MODEL: dict[str, Any] = {
    "name": "测试模型",
    "model_type": "chat",
    "litellm_model": "openai/gpt-4o",
    "context_length": 128000,
    "max_output": 4096,
}

# 调用每个占位接口用的合法请求（方法、路径、请求体）。GET 的查询参数写在路径里。
PLACEHOLDER_CALLS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/models", None),
    ("POST", "/api/v1/models", _CHAT_MODEL),
    ("GET", "/api/v1/models/1", None),
    ("PATCH", "/api/v1/models/1", {}),
    ("GET", "/api/v1/models/1/refs", None),
    ("POST", "/api/v1/models/1:disable", None),
    ("POST", "/api/v1/models/1:enable", None),
    ("DELETE", "/api/v1/models/1", None),
    ("POST", "/api/v1/models/1:test", None),
    ("POST", "/api/v1/models:test", {**_CHAT_MODEL, "model_id": 1}),
    (
        "POST",
        "/api/v1/models/1/chat",
        {"messages": [{"role": "user", "content": "你好"}]},
    ),
    (
        "GET",
        "/api/v1/usage/summary?from=2026-01-01T00:00:00Z&to=2026-01-31T00:00:00Z&group_by=model",
        None,
    ),
    (
        "GET",
        "/api/v1/usage/daily?from=2026-01-01T00:00:00Z&to=2026-01-31T00:00:00Z&tz=UTC",
        None,
    ),
    ("GET", "/api/v1/usage/meta", None),
]


@pytest_asyncio.fixture
async def admin_client(client: AsyncClient, session) -> AsyncClient:
    """系统管理员（拥有 console.use、model.manage、usage.view），Cookie 设在 client 上。"""
    user = await make_user(session, role=Role.sys_admin)
    client.cookies.update(auth_cookies(user))
    return client


def test_p2_operations_in_openapi(app) -> None:
    paths = app.openapi()["paths"]
    actual: set[tuple[str, str]] = set()
    for path, methods in paths.items():
        if not path.startswith("/api/v1"):
            continue
        for method in methods:
            actual.add((method.upper(), path))
    assert EXPECTED_OPERATIONS <= actual


@pytest.mark.parametrize(
    ("method", "path", "body"),
    PLACEHOLDER_CALLS,
    ids=[f"{m} {p}" for m, p, _ in PLACEHOLDER_CALLS],
)
async def test_placeholder_returns_not_implemented(
    admin_client: AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    response = await admin_client.request(method, path, json=body)
    assert response.status_code == 501
    assert response.json()["code"] == "NOT_IMPLEMENTED"


def test_sse_events_in_components(app) -> None:
    schemas = app.openapi()["components"]["schemas"]
    for name in (
        "SseEvent",
        "RunStartedEvent",
        "TextDeltaEvent",
        "ReasoningDeltaEvent",
        "RunFinishedEvent",
        "RunErrorEvent",
    ):
        assert name in schemas
    assert set(schemas["RunFinishedEvent"]["properties"]) == {
        "type",
        "input_tokens",
        "output_tokens",
        "first_token_ms",
        "total_ms",
    }
    assert set(schemas["RunErrorEvent"]["properties"]) == {"type", "code", "message"}
    assert schemas["SseEvent"]["discriminator"]["propertyName"] == "type"


def test_model_out_has_no_secret_fields(app) -> None:
    props = set(app.openapi()["components"]["schemas"]["ModelOut"]["properties"])
    assert {name for name in props if "key" in name} == {"api_key_last4"}


def test_p2_schema_field_names(app) -> None:
    schemas = app.openapi()["components"]["schemas"]

    assert set(schemas["ModelOut"]["properties"]) == {
        "id",
        "name",
        "model_type",
        "litellm_model",
        "api_base",
        "api_key_last4",
        "description",
        "context_length",
        "max_output",
        "cap_vision",
        "cap_tools",
        "cap_thinking",
        "embedding_dim",
        "embedding_max_input",
        "embedding_batch_size",
        "default_params",
        "first_token_timeout_s",
        "total_timeout_s",
        "max_retries",
        "rate_qps",
        "rate_concurrency",
        "status",
        "ref_counts",
        "fields_locked",
        "last_test_at",
        "last_test_ok",
        "last_test_message",
        "created_at",
        "updated_at",
    }
    assert set(schemas["ModelCreate"]["properties"]) == {
        "name",
        "model_type",
        "litellm_model",
        "api_base",
        "api_key",
        "description",
        "context_length",
        "max_output",
        "cap_vision",
        "cap_tools",
        "cap_thinking",
        "embedding_dim",
        "embedding_max_input",
        "embedding_batch_size",
        "default_params",
        "first_token_timeout_s",
        "total_timeout_s",
        "max_retries",
        "rate_qps",
        "rate_concurrency",
    }
    assert set(schemas["ModelTestIn"]["properties"]) == set(
        schemas["ModelCreate"]["properties"]
    ) | {"model_id"}
    assert set(schemas["ModelRefOut"]["properties"]) == {
        "resource_id",
        "type",
        "type_label",
        "name",
        "owner",
        "org",
    }
    assert set(schemas["ModelTestResult"]["properties"]) == {
        "ok",
        "latency_ms",
        "first_token_ms",
        "reply",
        "returned_model",
        "dims",
        "error_code",
        "error_message",
        "tested_at",
    }
    assert set(schemas["PlaygroundChatIn"]["properties"]) == {"messages", "params"}
    assert set(schemas["UsageSummaryOut"]["properties"]) == {"totals", "items", "total"}
    assert set(schemas["UsageDailyOut"]["properties"]) == {"items"}
    assert set(schemas["UsageMetaOut"]["properties"]) == {"sources"}
