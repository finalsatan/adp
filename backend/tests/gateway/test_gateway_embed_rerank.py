"""模型调用层：向量化与重排的成功/失败用例与用量记录（P2-05）。"""

from types import SimpleNamespace

import litellm
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.gateway import service
from app.gateway.errors import ModelCallError
from app.gateway.service import CallMeta
from app.gateway.tables import ModelUsage
from app.models.tables import Model
from tests.factories import make_org, make_user


async def make_model(session: AsyncSession, **overrides) -> Model:
    user = await make_user(session, role=Role.sys_admin)
    fields = {
        "name": "测试模型",
        "status": "enabled",
        "created_by": user.id,
        "model_type": "embedding",
        "litellm_model": "openai/text-embedding-3-small",
        "cap_thinking": False,
        "default_params": {},
        "first_token_timeout_s": 30,
        "total_timeout_s": 300,
        "max_retries": 2,
    }
    fields.update(overrides)
    model = Model(**fields)
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def meta_with_user(session: AsyncSession, source: str, source_id: int) -> CallMeta:
    org = await make_org(session, f"组织-{source}")
    user = await make_user(session, role=Role.builder, org=org)
    return CallMeta(source=source, source_id=source_id, user_id=user.id, org_id=org.id)


async def test_embed_success(app, session: AsyncSession, monkeypatch) -> None:
    model = await make_model(session)
    meta = await meta_with_user(session, "ingest", 42)

    # 用 LiteLLM 的 mock_response 模拟上游，不访问网络。
    real_aembedding = litellm.aembedding

    async def fake_aembedding(**kwargs):
        return await real_aembedding(**kwargs, mock_response=[0.1, 0.2, 0.3])

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)

    result = await service.embed(
        app.state.sessionmaker, app.state.redis, meta, model.id, ["hello world"]
    )

    assert len(result.vectors) == 1
    assert result.vectors[0] == [0.1, 0.2, 0.3]
    assert result.input_tokens is not None and result.input_tokens > 0

    row = (await session.execute(sa.select(ModelUsage))).scalar_one()
    assert row.ok is True
    assert row.source == "ingest"
    assert row.source_id == 42
    assert row.user_id == meta.user_id
    assert row.org_id == meta.org_id
    assert row.model_id == model.id


async def test_embed_failure(app, session: AsyncSession, monkeypatch) -> None:
    model = await make_model(session)
    meta = await meta_with_user(session, "ingest", 7)

    async def fake_aembedding(**kwargs):
        raise litellm.AuthenticationError(
            message="bad key", model="text-embedding-3-small", llm_provider="openai"
        )

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)

    with pytest.raises(ModelCallError) as exc:
        await service.embed(app.state.sessionmaker, app.state.redis, meta, model.id, ["hello"])
    assert exc.value.error_code == "UPSTREAM_AUTH"

    row = (await session.execute(sa.select(ModelUsage))).scalar_one()
    assert row.ok is False
    assert row.error_code == "UPSTREAM_AUTH"
    assert row.source == "ingest"
    assert row.source_id == 7


async def test_rerank_success(app, session: AsyncSession, monkeypatch) -> None:
    model = await make_model(session, model_type="rerank", litellm_model="cohere/rerank-v3.5")
    meta = await meta_with_user(session, "kb_hit_test", 3)

    async def fake_arerank(**kwargs):
        assert kwargs["top_n"] == 2
        return SimpleNamespace(
            results=[
                SimpleNamespace(index=1, relevance_score=0.9),
                SimpleNamespace(index=0, relevance_score=0.2),
            ]
        )

    monkeypatch.setattr(litellm, "arerank", fake_arerank)

    result = await service.rerank(
        app.state.sessionmaker,
        app.state.redis,
        meta,
        model.id,
        "查询",
        ["文档一", "文档二"],
        top_n=2,
    )

    assert [(item.index, item.score) for item in result.results] == [(1, 0.9), (0, 0.2)]
    assert result.input_tokens is not None and result.input_tokens > 0

    row = (await session.execute(sa.select(ModelUsage))).scalar_one()
    assert row.ok is True
    assert row.source == "kb_hit_test"
    assert row.source_id == 3
    assert row.user_id == meta.user_id
    assert row.org_id == meta.org_id


async def test_rerank_failure(app, session: AsyncSession, monkeypatch) -> None:
    model = await make_model(session, model_type="rerank", litellm_model="cohere/rerank-v3.5")
    meta = await meta_with_user(session, "kb_hit_test", 9)

    async def fake_arerank(**kwargs):
        raise litellm.BadRequestError(
            message="bad request", model="rerank-v3.5", llm_provider="cohere"
        )

    monkeypatch.setattr(litellm, "arerank", fake_arerank)

    with pytest.raises(ModelCallError) as exc:
        await service.rerank(
            app.state.sessionmaker, app.state.redis, meta, model.id, "查询", ["文档"]
        )
    assert exc.value.error_code == "UPSTREAM_BAD_REQUEST"

    row = (await session.execute(sa.select(ModelUsage))).scalar_one()
    assert row.ok is False
    assert row.error_code == "UPSTREAM_BAD_REQUEST"
