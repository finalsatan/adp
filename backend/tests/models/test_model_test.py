"""连通测试接口：三种模型、错误映射、超时、写回、表单不落库、密钥复用与权限（P2-06）。"""

import asyncio
from typing import Any

import litellm
import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.gateway.tables import ModelUsage
from app.models import testing
from tests.factories import auth_cookies, make_user

CHAT_MODEL: dict[str, Any] = {
    "name": "测试对话模型",
    "model_type": "chat",
    "litellm_model": "openai/gpt-4o",
    "context_length": 128000,
    "max_output": 4096,
}

EMBEDDING_MODEL: dict[str, Any] = {
    "name": "测试向量模型",
    "model_type": "embedding",
    "litellm_model": "dashscope/text-embedding-v3",
    "embedding_dim": 1024,
    "embedding_max_input": 8192,
    "embedding_batch_size": 10,
}

RERANK_MODEL: dict[str, Any] = {
    "name": "测试重排模型",
    "model_type": "rerank",
    "litellm_model": "cohere/rerank-v3.5",
}


@pytest_asyncio.fixture
async def admin(session: AsyncSession):
    return await make_user(session, role=Role.sys_admin)


@pytest_asyncio.fixture
async def admin_client(client: AsyncClient, admin) -> AsyncClient:
    client.cookies.update(auth_cookies(admin))
    return client


async def _create_model(client: AsyncClient, body: dict[str, Any]) -> dict:
    response = await client.post("/api/v1/models", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _usage_rows(session: AsyncSession) -> list[ModelUsage]:
    return list((await session.execute(sa.select(ModelUsage).order_by(ModelUsage.id))).scalars())


def _mock_chat(monkeypatch, reply: str = "你好，我是助手", captured: dict | None = None):
    real = litellm.acompletion

    async def fake(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return await real(**kwargs, mock_response=reply)

    monkeypatch.setattr(litellm, "acompletion", fake)


def _mock_embedding(monkeypatch, vector: list[float]):
    real = litellm.aembedding

    async def fake(**kwargs):
        return await real(**kwargs, mock_response=vector)

    monkeypatch.setattr(litellm, "aembedding", fake)


async def test_chat_success_writes_back_and_records_usage(
    admin_client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """对话模型成功：reply 有值，写回 last_test_*，产生一条 source=model_test 的用量。"""
    model = await _create_model(admin_client, CHAT_MODEL)
    _mock_chat(monkeypatch)

    response = await admin_client.post(f"/api/v1/models/{model['id']}:test")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["reply"] == "你好，我是助手"
    assert body["error_code"] is None
    assert body["latency_ms"] >= 0
    assert body["tested_at"]

    detail = (await admin_client.get(f"/api/v1/models/{model['id']}")).json()
    assert detail["last_test_ok"] is True
    assert detail["last_test_at"] is not None
    assert detail["last_test_message"] is None

    rows = await _usage_rows(session)
    assert len(rows) == 1
    assert rows[0].source == "model_test"
    assert rows[0].model_id == model["id"]
    assert rows[0].ok is True


async def test_chat_passes_max_tokens_and_disables_thinking(
    admin_client: AsyncClient, monkeypatch
) -> None:
    """对话测试传给 LiteLLM 的 max_tokens 为 16，且关闭了深度思考。"""
    model = await _create_model(
        admin_client,
        {**CHAT_MODEL, "litellm_model": "deepseek/deepseek-chat", "cap_thinking": True},
    )
    captured: dict = {}
    _mock_chat(monkeypatch, captured=captured)

    response = await admin_client.post(f"/api/v1/models/{model['id']}:test")
    assert response.json()["ok"] is True
    assert captured["max_tokens"] == 16
    assert captured["thinking"] == {"type": "disabled"}


async def test_embedding_success(
    admin_client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """向量化模型成功：dims 与登记一致。"""
    model = await _create_model(admin_client, EMBEDDING_MODEL)
    _mock_embedding(monkeypatch, [0.1] * 1024)

    response = await admin_client.post(f"/api/v1/models/{model['id']}:test")
    body = response.json()
    assert body["ok"] is True
    assert body["dims"] == 1024
    assert (await _usage_rows(session))[0].source == "model_test"


async def test_embedding_dim_mismatch(
    admin_client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """返回维度与登记的 embedding_dim 不一致：ok=false、DIM_MISMATCH。"""
    model = await _create_model(admin_client, EMBEDDING_MODEL)
    _mock_embedding(monkeypatch, [0.1, 0.2, 0.3])

    response = await admin_client.post(f"/api/v1/models/{model['id']}:test")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["dims"] == 3
    assert body["error_code"] == "DIM_MISMATCH"
    assert "3" in body["error_message"]
    assert "1024" in body["error_message"]

    # 失败也写回 last_test_*，并记一条失败测试的用量（上游调用本身成功，故 ok=true）。
    detail = (await admin_client.get(f"/api/v1/models/{model['id']}")).json()
    assert detail["last_test_ok"] is False
    assert detail["last_test_message"] == body["error_message"]
    rows = await _usage_rows(session)
    assert len(rows) == 1
    assert rows[0].source == "model_test"


async def test_rerank_success(
    admin_client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """重排模型成功。"""
    model = await _create_model(admin_client, RERANK_MODEL)

    async def fake_arerank(**kwargs):
        assert len(kwargs["documents"]) == 2
        return type(
            "R",
            (),
            {
                "results": [
                    type("Item", (), {"index": 1, "relevance_score": 0.9})(),
                    type("Item", (), {"index": 0, "relevance_score": 0.2})(),
                ]
            },
        )()

    monkeypatch.setattr(litellm, "arerank", fake_arerank)

    response = await admin_client.post(f"/api/v1/models/{model['id']}:test")
    assert response.json()["ok"] is True
    assert (await _usage_rows(session))[0].source == "model_test"


async def test_upstream_auth_error(admin_client: AsyncClient, monkeypatch) -> None:
    """401 返回 UPSTREAM_AUTH 和中文说明，接口仍是 200 + ok=false。"""
    model = await _create_model(admin_client, CHAT_MODEL)

    async def fake(**kwargs):
        raise litellm.AuthenticationError(
            message="Incorrect API key", model="gpt-4o", llm_provider="openai"
        )

    monkeypatch.setattr(litellm, "acompletion", fake)

    response = await admin_client.post(f"/api/v1/models/{model['id']}:test")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "UPSTREAM_AUTH"
    assert "密钥" in body["error_message"]


async def test_test_timeout(admin_client: AsyncClient, monkeypatch) -> None:
    """超过测试超时返回超时错误（把测试超时调小，避免等 10 秒）。"""
    model = await _create_model(admin_client, CHAT_MODEL)
    monkeypatch.setattr(testing, "TEST_TIMEOUT_S", 0.2)

    async def fake(**kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(litellm, "acompletion", fake)

    response = await admin_client.post(f"/api/v1/models/{model['id']}:test")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] in ("FIRST_TOKEN_TIMEOUT", "TOTAL_TIMEOUT")


async def test_disabled_saved_model_can_be_tested(admin_client: AsyncClient, monkeypatch) -> None:
    """停用的模型也能测试（管理员排查问题）。"""
    model = await _create_model(admin_client, CHAT_MODEL)
    assert (await admin_client.post(f"/api/v1/models/{model['id']}:disable")).status_code == 200
    _mock_chat(monkeypatch)

    response = await admin_client.post(f"/api/v1/models/{model['id']}:test")
    assert response.status_code == 200
    assert response.json()["ok"] is True


async def test_form_test_does_not_persist(
    admin_client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """表单测试不落库：不改库里任何模型字段，也不新建模型。"""
    model = await _create_model(admin_client, CHAT_MODEL)
    before = (await admin_client.get(f"/api/v1/models/{model['id']}")).json()
    _mock_chat(monkeypatch)

    response = await admin_client.post(
        "/api/v1/models:test",
        json={**CHAT_MODEL, "name": "临时名字", "model_id": model["id"]},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    after = (await admin_client.get(f"/api/v1/models/{model['id']}")).json()
    assert after == before
    assert (await admin_client.get("/api/v1/models")).json()["total"] == 1


async def test_form_uses_saved_key_when_model_id(
    admin_client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """编辑时不填密钥、带 model_id：用已保存的密钥调用 LiteLLM。"""
    model = await _create_model(admin_client, {**CHAT_MODEL, "api_key": "sk-saved-key-1234"})
    captured: dict = {}
    _mock_chat(monkeypatch, captured=captured)

    # 表单里 api_key 为空，带 model_id。
    response = await admin_client.post(
        "/api/v1/models:test", json={**CHAT_MODEL, "model_id": model["id"]}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["api_key"] == "sk-saved-key-1234"

    # 用量归到该模型。
    rows = await _usage_rows(session)
    assert rows[0].model_id == model["id"]


async def test_form_without_model_id_records_unsaved_name(
    admin_client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """不带 model_id 的表单测试：用量 model_id 为空、model_name 为表单显示名称。"""
    _mock_chat(monkeypatch)

    response = await admin_client.post(
        "/api/v1/models:test", json={**CHAT_MODEL, "name": "未保存的模型"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    rows = await _usage_rows(session)
    assert len(rows) == 1
    assert rows[0].model_id is None
    assert rows[0].model_name == "未保存的模型"
    assert rows[0].source == "model_test"


async def test_form_internal_host_rejected(admin_client: AsyncClient) -> None:
    """表单测试的接口地址同样做 check_model_host，内网地址被拒（422）。"""
    response = await admin_client.post(
        "/api/v1/models:test", json={**CHAT_MODEL, "api_base": "http://127.0.0.1:8000"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "MODEL_HOST_NOT_ALLOWED"


async def test_builder_can_test_saved_but_not_form(
    admin_client: AsyncClient, session: AsyncSession, org_tree, monkeypatch
) -> None:
    """搭建者能测已保存的模型（console.use），不能调用表单测试（model.manage）。"""
    model = await _create_model(admin_client, CHAT_MODEL)
    _mock_chat(monkeypatch)

    builder = await make_user(session, role=Role.builder, org=org_tree.rd)
    admin_client.cookies.clear()
    admin_client.cookies.update(auth_cookies(builder))

    saved = await admin_client.post(f"/api/v1/models/{model['id']}:test")
    assert saved.status_code == 200
    assert saved.json()["ok"] is True

    form = await admin_client.post("/api/v1/models:test", json=CHAT_MODEL)
    assert form.status_code == 403
    assert form.json()["code"] == "PERMISSION_DENIED"


async def test_unknown_saved_model_returns_404(admin_client: AsyncClient) -> None:
    """不存在的模型：404。"""
    response = await admin_client.post("/api/v1/models/999999:test")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
