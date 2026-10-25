"""命中测试接口测试（P3-13）：各步骤结果、临时覆盖配置、记录不保存与权限。"""

from __future__ import annotations

import uuid

import litellm
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from tests.factories import auth_cookies, make_org, make_user
from tests.kb.test_retrieval import _add_chunk, _make_doc, fake_embedding


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_embedding_model(
    session: AsyncSession, owner, *, dim: int = 4, status: str = "enabled"
) -> Model:
    model = Model(
        name=f"向量模型-{uuid.uuid4().hex[:8]}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=dim,
        embedding_max_input=8192,
        status=status,
        created_by=owner.id,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def create_kb(client: AsyncClient, *, name: str, model_id: int):
    return await client.post(
        "/api/v1/kbs",
        json={
            "kind": "doc",
            "name": name,
            "embedding_model_id": model_id,
            "visibility": "org",
        },
    )


async def _setup_kb(client, session) -> tuple[int, int]:
    org = await make_org(session, f"命中组织-{uuid.uuid4().hex[:6]}")
    builder = await make_user(session, Role.builder, org, username=f"hit_{uuid.uuid4().hex[:6]}")
    model = await make_embedding_model(session, builder)
    login_as(client, builder)
    resp = await create_kb(client, name=f"命中库-{uuid.uuid4().hex[:6]}", model_id=model.id)
    assert resp.status_code == 201, resp.text
    kb_id = int(resp.json()["id"])
    doc = await _make_doc(session, kb_id=kb_id, user_id=builder.id)
    await _add_chunk(
        session,
        kb_id=kb_id,
        doc_id=doc,
        content="长江大桥的报销标准",
        vector=[1, 0, 0, 0],
        title="差旅制度",
        positions=[{"page": 3, "bbox": [0, 0, 1, 1]}],
    )
    return kb_id, builder.id


async def test_hit_test_steps_scores_timings(
    client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    kb_id, _ = await _setup_kb(client, session)
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))

    resp = await client.post(f"/api/v1/kbs/{kb_id}/hit-test", json={"question": "长江大桥"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data["steps"]) == {"vector", "fulltext", "fusion", "rerank"}
    assert set(data["timings"]) == {"vector_ms", "fulltext_ms", "fusion_ms", "rerank_ms"}
    assert all(value >= 0 for value in data["timings"].values())

    vector_hit = data["steps"]["vector"][0]
    assert vector_hit["chunk_id"] > 0
    assert vector_hit["doc_name"].endswith(".pdf")
    assert vector_hit["page_no"] == 3
    assert vector_hit["positions"][0]["page"] == 3
    assert vector_hit["scores"]["vector"] == pytest.approx(1.0, abs=1e-6)
    assert data["steps"]["fulltext"][0]["scores"]["keyword"] == pytest.approx(1.0)
    assert data["final"], "默认配置（混合）应有结果"


async def test_hit_test_temporary_override_and_not_saved(
    client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    kb_id, _ = await _setup_kb(client, session)
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))

    override = {
        "mode": "vector",
        "vector_weight": 1,
        "fulltext_weight": 0,
        "top_k": 5,
        "score_threshold": 0,
        "rerank": {"enabled": False, "top_n": 5},
        "adjacent_n": 0,
    }
    first = await client.post(
        f"/api/v1/kbs/{kb_id}/hit-test", json={"question": "长江大桥", "config": override}
    )
    assert first.status_code == 200, first.text
    # 临时覆盖生效：只用向量时全文召回为空。
    assert first.json()["steps"]["fulltext"] == []

    # 记录不保存：第二次查询不受上一次影响，知识库默认配置不变。
    second = await client.post(f"/api/v1/kbs/{kb_id}/hit-test", json={"question": "长江大桥"})
    assert second.status_code == 200, second.text
    assert second.json()["steps"]["fulltext"], "默认混合配置应执行全文召回"
    kb_row = await session.get(KnowledgeBase, kb_id)
    assert kb_row is not None
    await session.refresh(kb_row)
    assert kb_row.retrieval_config == {}


async def test_retrieval_config_get_put(client: AsyncClient, session: AsyncSession) -> None:
    kb_id, _ = await _setup_kb(client, session)

    resp = await client.get(f"/api/v1/kbs/{kb_id}/retrieval-config")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "hybrid"
    assert resp.json()["top_k"] == 10

    body = {
        "mode": "vector",
        "vector_weight": 1,
        "fulltext_weight": 0,
        "top_k": 7,
        "score_threshold": 0.3,
        "rerank": {"enabled": False, "top_n": 7},
        "adjacent_n": 1,
    }
    resp = await client.put(f"/api/v1/kbs/{kb_id}/retrieval-config", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["top_k"] == 7 and resp.json()["mode"] == "vector"

    resp = await client.get(f"/api/v1/kbs/{kb_id}/retrieval-config")
    assert resp.json()["top_k"] == 7
    kb_row = await session.get(KnowledgeBase, kb_id)
    assert kb_row is not None
    await session.refresh(kb_row)
    assert kb_row.retrieval_config["top_k"] == 7


async def test_hit_test_permission(client: AsyncClient, session: AsyncSession, monkeypatch) -> None:
    kb_id, _ = await _setup_kb(client, session)
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))

    # 终端用户没有 console.use → 403。
    end_org = await make_org(session, f"终端组织-{uuid.uuid4().hex[:6]}")
    end_user = await make_user(
        session, Role.end_user, end_org, username=f"end_{uuid.uuid4().hex[:6]}"
    )
    login_as(client, end_user)
    resp = await client.post(f"/api/v1/kbs/{kb_id}/hit-test", json={"question": "长江大桥"})
    assert resp.status_code == 403

    # 其他组织看不到该库 → 404（不暴露资源是否存在，P1-12）。
    other_org = await make_org(session, f"其他组织-{uuid.uuid4().hex[:6]}")
    outsider = await make_user(
        session, Role.builder, other_org, username=f"out_{uuid.uuid4().hex[:6]}"
    )
    login_as(client, outsider)
    resp = await client.post(f"/api/v1/kbs/{kb_id}/hit-test", json={"question": "长江大桥"})
    assert resp.status_code == 404
