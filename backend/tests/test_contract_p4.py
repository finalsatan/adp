"""第四期接口契约：路径清单、占位返回、配置判别联合、对话 SSE 事件、字段名。"""

from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.schemas import Role
from tests.factories import auth_cookies, make_user

# 第四期全部“方法 + 路径”，路径参数按 OpenAPI 的写法（不带 int 转换器）。
EXPECTED_OPERATIONS: set[tuple[str, str]] = {
    # 应用
    ("GET", "/api/v1/apps"),
    ("POST", "/api/v1/apps"),
    ("GET", "/api/v1/apps/{id}"),
    ("PATCH", "/api/v1/apps/{id}"),
    ("DELETE", "/api/v1/apps/{id}"),
    ("POST", "/api/v1/apps/{id}:disable"),
    ("POST", "/api/v1/apps/{id}:enable"),
    ("PATCH", "/api/v1/apps/{id}/visibility"),
    ("GET", "/api/v1/apps/{id}/members"),
    ("POST", "/api/v1/apps/{id}/members"),
    ("DELETE", "/api/v1/apps/{id}/members/{user_id}"),
    ("GET", "/api/v1/apps/{id}/usage-scope"),
    ("PUT", "/api/v1/apps/{id}/usage-scope"),
    ("GET", "/api/v1/my/apps"),
    # 草稿与版本
    ("GET", "/api/v1/apps/{id}/draft"),
    ("PUT", "/api/v1/apps/{id}/draft"),
    ("GET", "/api/v1/apps/{id}/prompt-preview"),
    ("GET", "/api/v1/apps/{id}/kb-candidates"),
    ("POST", "/api/v1/apps/{id}:publish"),
    ("GET", "/api/v1/apps/{id}/versions"),
    ("GET", "/api/v1/apps/{id}/versions/{v}"),
    ("POST", "/api/v1/apps/{id}/versions/{v}:restore"),
    # 调试
    ("POST", "/api/v1/apps/{id}/debug/chat"),
    ("GET", "/api/v1/apps/{id}/debug/chunks/{chunk}/preview"),
    # 对话日志与反馈
    ("GET", "/api/v1/apps/{id}/logs/conversations"),
    ("GET", "/api/v1/apps/{id}/logs/conversations/{conv}"),
    ("GET", "/api/v1/apps/{id}/logs/answers/{answer}/citations/{n}/preview"),
    ("GET", "/api/v1/apps/{id}/feedback"),
    # 终端用户
    ("GET", "/api/v1/chat/apps/{id}"),
    ("GET", "/api/v1/chat/apps/{id}/conversations"),
    ("POST", "/api/v1/chat/apps/{id}/conversations"),
    ("PATCH", "/api/v1/conversations/{id}"),
    ("DELETE", "/api/v1/conversations/{id}"),
    ("GET", "/api/v1/conversations/{id}/turns"),
    ("POST", "/api/v1/conversations/{id}/turns"),
    ("POST", "/api/v1/turns/{id}:regenerate"),
    ("PUT", "/api/v1/answers/{id}/feedback"),
    ("DELETE", "/api/v1/answers/{id}/feedback"),
    ("GET", "/api/v1/answers/{id}/citations/{n}/preview"),
    ("GET", "/api/v1/answers/{id}/citations/{n}/file"),
}

# 调用每个占位接口用的合法请求（方法、路径、请求体）。
# 已实现的接口（P4-03 起）不在这里；剩余占位接口都返回 501。
PLACEHOLDER_CALLS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/apps/1/feedback", None),
    ("PUT", "/api/v1/answers/1/feedback", {"rating": "up"}),
    ("DELETE", "/api/v1/answers/1/feedback", None),
]

CHAT_SSE_TYPES = {
    "RUN_STARTED",
    "STEP_STARTED",
    "STEP_FINISHED",
    "CITATIONS",
    "TEXT_DELTA",
    "REASONING_DELTA",
    "SUGGESTIONS",
    "TRACE",
    "RUN_FINISHED",
    "RUN_ERROR",
}


def test_p4_operations_in_openapi(app) -> None:
    paths = app.openapi()["paths"]
    actual: set[tuple[str, str]] = set()
    for path, methods in paths.items():
        if not path.startswith("/api/v1"):
            continue
        for method in methods:
            actual.add((method.upper(), path))
    assert EXPECTED_OPERATIONS <= actual


@pytest_asyncio.fixture
async def admin_cookies(session) -> dict[str, str]:
    """占位接口都要求登录，用系统管理员访问才能走到占位实现。"""
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


def test_app_config_discriminated_union(app) -> None:
    schemas = app.openapi()["components"]["schemas"]
    assert "KbQaAppConfig" in schemas
    assert schemas["KbQaAppConfig"]["properties"]["app_type"]["const"] == "kb_qa"
    # 判别联合在 config 字段上，按 app_type 区分，本阶段只有 kb_qa
    config = schemas["DraftOut"]["properties"]["config"]
    assert config["discriminator"]["propertyName"] == "app_type"
    assert set(config["discriminator"]["mapping"]) == {"kb_qa"}
    assert set(schemas["KbQaAppConfig"]["properties"]) == {
        "schema_version",
        "app_type",
        "model",
        "system_prompt",
        "intent",
        "rewrite",
        "memory_turns",
        "knowledge",
        "reply",
    }
    assert set(schemas["AppKnowledgeConfig"]["properties"]) == {"kbs", "doc", "qa"}
    assert set(schemas["AppKbRef"]["properties"]) == {
        "kb_id",
        "retrieval",
        "filter",
        "qa_category_ids",
    }
    assert set(schemas["AppDocConfig"]["properties"]) == {
        "top_k",
        "score_threshold",
        "rerank",
        "neighbor_chunks",
    }
    assert set(schemas["AppReplyConfig"]["properties"]) == {
        "fallback_text",
        "opening",
        "opening_questions",
        "suggestions",
        "allow_download",
    }
    # 第六期才生效的配置字段先保留
    assert "qa" in schemas["AppKnowledgeConfig"]["properties"]
    assert "suggestions" in schemas["AppReplyConfig"]["properties"]
    assert "thinking" in schemas["AppModelConfig"]["properties"]


def test_chat_sse_events_in_components(app) -> None:
    schemas = app.openapi()["components"]["schemas"]
    for name in (
        "ChatSseEvent",
        "ChatRunStartedEvent",
        "StepStartedEvent",
        "StepFinishedEvent",
        "CitationsEvent",
        "SuggestionsEvent",
        "TraceEvent",
        "ChatRunFinishedEvent",
    ):
        assert name in schemas
    assert schemas["ChatSseEvent"]["discriminator"]["propertyName"] == "type"
    assert set(schemas["ChatSseEvent"]["discriminator"]["mapping"]) == CHAT_SSE_TYPES
    assert set(schemas["ChatRunStartedEvent"]["properties"]) == {
        "type",
        "run_id",
        "conversation_id",
        "turn_id",
        "answer_id",
    }
    assert set(schemas["ChatRunFinishedEvent"]["properties"]) == {
        "type",
        "kind",
        "status",
        "input_tokens",
        "output_tokens",
        "first_token_ms",
        "total_ms",
    }
    assert set(schemas["StepFinishedEvent"]["properties"]) == {"type", "step", "duration_ms"}
    assert set(schemas["CitationsEvent"]["properties"]) == {"type", "citations"}
    assert set(schemas["TraceEvent"]["properties"]) == {"type", "trace"}


def test_p4_schema_field_names(app) -> None:
    schemas = app.openapi()["components"]["schemas"]

    assert set(schemas["AppOut"]["properties"]) == {
        "id",
        "name",
        "description",
        "app_type",
        "org",
        "owner",
        "visibility",
        "status",
        "published_version",
        "draft_has_changes",
        "draft_updated_at",
        "usage_scope",
        "chat_path",
        "created_at",
        "updated_at",
        "editable",
        "manageable",
    }
    assert set(schemas["AnswerOut"]["properties"]) == {
        "id",
        "turn_id",
        "seq",
        "version_no",
        "kind",
        "status",
        "content",
        "reasoning",
        "citations",
        "cited",
        "suggestions",
        "error_code",
        "error_message",
        "input_tokens",
        "output_tokens",
        "first_token_ms",
        "total_ms",
        "feedback",
        "trace",
        "created_at",
        "updated_at",
    }
    assert set(schemas["CitationOut"]["properties"]) == {
        "n",
        "type",
        "kb_id",
        "kb_name",
        "score",
        "qa_id",
        "question",
        "answer",
        "direct",
        "doc_id",
        "doc_name",
        "file_type",
        "chunk_ids",
        "generation",
        "content",
        "positions",
        "edited",
    }
    assert set(schemas["FeedbackPage"]["properties"]) == {"items", "total", "summary"}
    assert set(schemas["FeedbackSummary"]["properties"]) == {"up", "down", "down_with_correction"}
    assert set(schemas["AppChatInfoOut"]["properties"]) == {
        "id",
        "name",
        "description",
        "opening",
        "opening_questions",
        "suggestions",
        "allow_download",
    }
    assert set(schemas["CitationPreviewOut"]["properties"]) == {
        "render",
        "file_type",
        "kb_id",
        "kb_name",
        "doc_id",
        "doc_name",
        "page_no",
        "pages",
        "positions",
        "content",
        "elements",
        "generation",
        "edited",
        "stale",
        "note",
        "download_allowed",
    }
    assert set(schemas["UsageScopeIn"]["properties"]) == {"scope", "org_ids", "user_ids"}
    assert set(schemas["KbCandidateOut"]["properties"]) == {
        "kb_id",
        "name",
        "kind",
        "embedding_model",
        "org",
        "visibility",
        "status",
        "added",
        "selectable",
        "reason",
    }


def test_citation_preview_response_content_types(app) -> None:
    preview = app.openapi()["paths"]["/api/v1/answers/{id}/citations/{n}/preview"]["get"]
    assert set(preview["responses"]["200"]["content"]) == {"application/json", "application/pdf"}
    file_resp = app.openapi()["paths"]["/api/v1/answers/{id}/citations/{n}/file"]["get"]
    assert "application/octet-stream" in file_resp["responses"]["200"]["content"]
    chat = app.openapi()["paths"]["/api/v1/conversations/{id}/turns"]["post"]
    assert "text/event-stream" in chat["responses"]["200"]["content"]


async def test_request_extra_fields_rejected(
    client: AsyncClient, admin_cookies: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/apps",
        json={
            "app_type": "kb_qa",
            "name": "契约应用",
            "visibility": "private",
            "unexpected": 1,
        },
        cookies=admin_cookies,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_action_routes_use_int_converter(
    client: AsyncClient, admin_cookies: dict[str, str]
) -> None:
    # 动作路由：id 是整数，动作后缀不能被当成 id 的一部分。
    # 应用管理已实现（P4-03），不存在的应用返回 404；未实现的动作仍是 501。
    assert (await client.post("/api/v1/apps/5:disable", cookies=admin_cookies)).status_code == 404
    assert (await client.post("/api/v1/apps/5:enable", cookies=admin_cookies)).status_code == 404
    assert (
        await client.post("/api/v1/apps/5:publish", json={"note": "x"}, cookies=admin_cookies)
    ).status_code == 404
    assert (
        await client.post("/api/v1/apps/5/versions/1:restore", cookies=admin_cookies)
    ).status_code == 404
    assert (
        await client.post("/api/v1/turns/5:regenerate", cookies=admin_cookies)
    ).status_code == 404
    # 非整数的 id 不匹配任何路由
    missing = await client.get("/api/v1/apps/abc", cookies=admin_cookies)
    assert missing.status_code == 404
