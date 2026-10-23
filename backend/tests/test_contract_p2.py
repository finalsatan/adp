"""第二期接口契约：路径清单、SSE 结构、字段名与密钥不泄露。"""

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


def test_p2_operations_in_openapi(app) -> None:
    paths = app.openapi()["paths"]
    actual: set[tuple[str, str]] = set()
    for path, methods in paths.items():
        if not path.startswith("/api/v1"):
            continue
        for method in methods:
            actual.add((method.upper(), path))
    assert EXPECTED_OPERATIONS <= actual


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
