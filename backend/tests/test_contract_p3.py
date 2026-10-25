"""第三期接口契约：路径清单、占位返回、SchemeConfig 判别联合、字段名与设置分组。"""

from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.schemas import Role
from tests.factories import auth_cookies, make_user

# 第三期全部“方法 + 路径”，路径参数按 OpenAPI 的写法（不带 int 转换器）。
EXPECTED_OPERATIONS: set[tuple[str, str]] = {
    # 知识库
    ("GET", "/api/v1/kbs"),
    ("POST", "/api/v1/kbs"),
    ("GET", "/api/v1/kbs/{id}"),
    ("PATCH", "/api/v1/kbs/{id}"),
    ("DELETE", "/api/v1/kbs/{id}"),
    ("POST", "/api/v1/kbs/{id}:disable"),
    ("POST", "/api/v1/kbs/{id}:enable"),
    ("GET", "/api/v1/kbs/{id}/refs"),
    ("PATCH", "/api/v1/kbs/{id}/visibility"),
    ("GET", "/api/v1/kbs/{id}/members"),
    ("POST", "/api/v1/kbs/{id}/members"),
    ("DELETE", "/api/v1/kbs/{id}/members/{user_id}"),
    # 检索与命中测试
    ("GET", "/api/v1/kbs/{id}/retrieval-config"),
    ("PUT", "/api/v1/kbs/{id}/retrieval-config"),
    ("POST", "/api/v1/kbs/{id}/hit-test"),
    # 文件夹与元数据
    ("GET", "/api/v1/kbs/{id}/folders"),
    ("POST", "/api/v1/kbs/{id}/folders"),
    ("PATCH", "/api/v1/folders/{id}"),
    ("DELETE", "/api/v1/folders/{id}"),
    ("GET", "/api/v1/kbs/{id}/metadata-fields"),
    ("POST", "/api/v1/kbs/{id}/metadata-fields"),
    ("PATCH", "/api/v1/metadata-fields/{id}"),
    ("DELETE", "/api/v1/metadata-fields/{id}"),
    # 文档
    ("POST", "/api/v1/kbs/{id}/documents:precheck"),
    ("POST", "/api/v1/kbs/{id}/documents"),
    ("GET", "/api/v1/kbs/{id}/documents"),
    ("GET", "/api/v1/documents/{id}"),
    ("PATCH", "/api/v1/documents/{id}"),
    ("DELETE", "/api/v1/documents/{id}"),
    ("POST", "/api/v1/documents:batch"),
    ("POST", "/api/v1/documents/{id}:retry"),
    ("POST", "/api/v1/documents/{id}:reparse"),
    ("GET", "/api/v1/documents/{id}/file"),
    ("GET", "/api/v1/documents/{id}/images/{name}"),
    ("GET", "/api/v1/documents/{id}/parse"),
    # 入库方案与引擎
    ("GET", "/api/v1/schemes"),
    ("GET", "/api/v1/schemes/{id}"),
    ("GET", "/api/v1/parse-engines"),
    ("GET", "/api/v1/kbs/{id}/default-schemes"),
    ("PUT", "/api/v1/kbs/{id}/default-schemes"),
}

# 调用每个占位接口用的合法请求（方法、路径、传给 client.request 的关键字参数）。
PLACEHOLDER_CALLS: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/api/v1/kbs/1/retrieval-config", {}),
    ("PUT", "/api/v1/kbs/1/retrieval-config", {"json": {}}),
    ("POST", "/api/v1/kbs/1/hit-test", {"json": {"question": "测试问题"}}),
    (
        "POST",
        "/api/v1/kbs/1/documents:precheck",
        {"json": {"files": [{"name": "a.pdf", "size": 100, "sha256": "a" * 64}]}},
    ),
    (
        "POST",
        "/api/v1/kbs/1/documents",
        {
            "files": {"files": ("a.pdf", b"data", "application/pdf")},
            "data": {"relative_paths": "", "schemes": "{}", "resolutions": "{}"},
        },
    ),
    ("GET", "/api/v1/kbs/1/documents", {}),
    ("GET", "/api/v1/documents/1", {}),
    ("PATCH", "/api/v1/documents/1", {"json": {"enabled": False}}),
    ("DELETE", "/api/v1/documents/1", {}),
    ("POST", "/api/v1/documents:batch", {"json": {"action": "delete", "doc_ids": [1]}}),
    ("POST", "/api/v1/documents/1:retry", {}),
    ("POST", "/api/v1/documents/1:reparse", {"json": {"mode": "reparse"}}),
    ("GET", "/api/v1/documents/1/file", {}),
    ("GET", "/api/v1/documents/1/images/a.png", {}),
    ("GET", "/api/v1/documents/1/parse", {}),
    ("GET", "/api/v1/schemes", {}),
    ("GET", "/api/v1/schemes/1", {}),
    ("GET", "/api/v1/parse-engines", {}),
    ("GET", "/api/v1/kbs/1/default-schemes", {}),
    ("PUT", "/api/v1/kbs/1/default-schemes", {"json": {"items": []}}),
]


def test_p3_operations_in_openapi(app) -> None:
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
    """占位接口要求登录，用系统管理员访问才能走到占位实现。"""
    user = await make_user(session, role=Role.sys_admin)
    return auth_cookies(user)


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    PLACEHOLDER_CALLS,
    ids=[f"{m} {p}" for m, p, _ in PLACEHOLDER_CALLS],
)
async def test_placeholder_returns_not_implemented(
    client: AsyncClient,
    method: str,
    path: str,
    kwargs: dict[str, Any],
    admin_cookies: dict[str, str],
) -> None:
    response = await client.request(method, path, cookies=admin_cookies, **kwargs)
    assert response.status_code == 501, response.text
    assert response.json()["code"] == "NOT_IMPLEMENTED"


def test_scheme_config_discriminated_union(app) -> None:
    schemas = app.openapi()["components"]["schemas"]
    assert "SchemeConfig" in schemas
    chunk = schemas["SchemeConfig"]["properties"]["chunk"]
    assert chunk["discriminator"]["propertyName"] == "mode"
    mapping = chunk["discriminator"]["mapping"]
    assert set(mapping) == {"auto", "heading", "length", "rule", "page", "whole", "semantic"}
    for name in (
        "ChunkAuto",
        "ChunkHeading",
        "ChunkLength",
        "ChunkRule",
        "ChunkPage",
        "ChunkWhole",
        "ChunkSemantic",
    ):
        assert name in schemas
    # 判别联合的每个成员都带自己的 mode 常量
    assert schemas["ChunkAuto"]["properties"]["mode"]["const"] == "auto"
    assert schemas["ChunkHeading"]["properties"]["mode"]["const"] == "heading"
    assert schemas["ChunkSemantic"]["properties"]["mode"]["const"] == "semantic"


def test_kb_out_field_names(app) -> None:
    props = set(app.openapi()["components"]["schemas"]["KbOut"]["properties"])
    assert props == {
        "id",
        "name",
        "description",
        "kind",
        "org",
        "owner",
        "visibility",
        "status",
        "embedding_model",
        "doc_count",
        "chunk_count",
        "created_at",
        "updated_at",
        "editable",
        "manageable",
    }


def test_p3_schema_field_names(app) -> None:
    schemas = app.openapi()["components"]["schemas"]

    assert set(schemas["KbCreate"]["properties"]) == {
        "kind",
        "name",
        "description",
        "embedding_model_id",
        "visibility",
        "org_id",
    }
    assert set(schemas["KbUpdate"]["properties"]) == {
        "name",
        "description",
        "kind",
        "embedding_model_id",
    }
    assert set(schemas["DocumentOut"]["properties"]) == {
        "id",
        "kb_id",
        "folder",
        "name",
        "file_type",
        "ext",
        "size",
        "status",
        "step",
        "progress",
        "failed_step",
        "error_code",
        "error_message",
        "queued_at",
        "started_at",
        "finished_at",
        "heartbeat_at",
        "scheme",
        "scheme_snapshot_name",
        "current_generation",
        "metadata",
        "enabled",
        "page_count",
        "chunk_count",
        "enhance_partial_failed",
        "edited",
        "uploaded_by",
        "run_by",
        "queue_position",
        "created_at",
    }
    assert set(schemas["RetrievalConfig"]["properties"]) == {
        "mode",
        "vector_weight",
        "fulltext_weight",
        "top_k",
        "score_threshold",
        "rerank",
        "adjacent_n",
    }
    assert set(schemas["HitTestOut"]["properties"]) == {"steps", "final", "timings"}
    assert set(schemas["ParseResultOut"]["properties"]) == {
        "kind",
        "markdown",
        "engine",
        "engine_version",
        "pages",
        "ocr_pages",
        "edited_by",
        "edited_at",
        "elements",
    }


def test_settings_upload_group(app) -> None:
    schemas = app.openapi()["components"]["schemas"]
    assert "upload" in schemas["SettingsOut"]["properties"]
    assert set(schemas["UploadSettings"]["properties"]) == {
        "max_file_size_mb",
        "parse_concurrency",
        "kb_parse_concurrency",
    }
    assert schemas["UploadSettings"]["properties"]["max_file_size_mb"]["default"] == 200
    assert schemas["UploadSettings"]["properties"]["parse_concurrency"]["default"] == 4
    assert schemas["UploadSettings"]["properties"]["kb_parse_concurrency"]["default"] == 2


async def test_request_extra_fields_rejected(
    client: AsyncClient, admin_cookies: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/kbs",
        json={
            "kind": "doc",
            "name": "契约库",
            "embedding_model_id": 1,
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
    # 知识库接口已实现（P3-04），不存在的知识库返回 404（而不是占位的 501）。
    disabled = await client.post("/api/v1/kbs/5:disable", cookies=admin_cookies)
    assert disabled.status_code == 404
    # 非整数的 id 不匹配任何路由
    missing = await client.get("/api/v1/kbs/abc", cookies=admin_cookies)
    assert missing.status_code == 404
