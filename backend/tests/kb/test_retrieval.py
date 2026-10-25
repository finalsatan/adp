"""检索服务函数测试（P3-13）。

连真实 PostgreSQL（ParadeDB 镜像）、Redis；向量与重排模型用固定结果的假实现
（monkeypatch `litellm.aembedding` / `litellm.arerank`），数据集固定、向量维度小。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import litellm
import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EmbeddingModelUnavailableError
from app.gateway.tables import ModelUsage
from app.kb import retrieval
from app.kb.index_table import create_index_table, drop_index_table, index_table_name
from app.kb.schemas import MetadataFilter, MetadataGroup, RetrievalConfig
from app.kb.tables import Chunk, Document, KbFolder, KbMetadataField, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from tests.worker import make_kb


def fake_embedding(vector: list[float]):
    """固定查询向量的假向量化。"""

    async def _fake(**kwargs):
        inputs = kwargs["input"]
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=list(vector)) for _ in inputs],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0),
        )

    return _fake


def fake_rerank(scores: list[float]):
    """按输入顺序返回固定相关度的假重排。"""

    async def _fake(**kwargs):
        return SimpleNamespace(
            results=[
                SimpleNamespace(index=index, relevance_score=score)
                for index, score in enumerate(scores)
            ]
        )

    return _fake


async def _prepare_kb(
    session: AsyncSession, *, dim: int = 4, name: str = "检索库"
) -> tuple[KnowledgeBase, int]:
    kb, user_id = await make_kb(session, name, dim=dim)
    await create_index_table(session, kb.id, dim)
    await session.commit()
    return kb, user_id


async def _make_doc(
    session: AsyncSession,
    *,
    kb_id: int,
    user_id: int,
    folder_id: int | None = None,
    metadata: dict | None = None,
) -> int:
    doc = Document(
        kb_id=kb_id,
        folder_id=folder_id,
        name=f"文档-{uuid.uuid4().hex[:6]}.pdf",
        file_type="pdf",
        ext="pdf",
        size=10,
        sha256=uuid.uuid4().hex * 2,
        status="done",
        uploaded_by=user_id,
        run_by=user_id,
        doc_metadata=metadata or {},
    )
    session.add(doc)
    await session.commit()
    return doc.id


async def _add_chunk(
    session: AsyncSession,
    *,
    kb_id: int,
    doc_id: int,
    content: str,
    vector: list[float],
    seq: int = 1,
    title: str | None = None,
    keywords: str | None = None,
    active: bool = True,
    positions: list | None = None,
    kind: str = "normal",
    edited: bool = False,
    generation: int = 1,
) -> int:
    chunk = Chunk(
        kb_id=kb_id,
        doc_id=doc_id,
        generation=generation,
        seq=seq,
        kind=kind,
        title_path=title,
        content=content,
        positions=positions,
        source="auto",
        edited=edited,
    )
    session.add(chunk)
    await session.flush()
    literal = "[" + ",".join(str(float(value)) for value in vector) + "]"
    await session.execute(
        text(
            f"INSERT INTO {index_table_name(kb_id)} "
            "(entry_type, chunk_id, doc_id, generation, active, embedding, title, body, keywords) "
            "VALUES ('chunk', :cid, :did, :gen, :active, :emb, :title, :body, :kw)"
        ),
        {
            "cid": chunk.id,
            "did": doc_id,
            "gen": generation,
            "active": active,
            "emb": literal,
            "title": title,
            "body": content,
            "kw": keywords,
        },
    )
    await session.commit()
    return chunk.id


async def _recall(
    app, session, kb_id: int, question: str, config: RetrievalConfig
) -> retrieval.RecallResult:
    return await retrieval.recall_and_fuse(
        app.state.sessionmaker,
        app.state.redis,
        kb_ids=[kb_id],
        question=question,
        config=config,
    )


# ---- 三种检索方式的分数（12.2）----


async def test_vector_fulltext_hybrid_scores(app, session, monkeypatch) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    a = await _add_chunk(
        session,
        kb_id=kb.id,
        doc_id=doc,
        content="长江大桥的报销标准",
        vector=[1, 0, 0, 0],
        seq=1,
        title="差旅制度",
    )
    b = await _add_chunk(
        session,
        kb_id=kb.id,
        doc_id=doc,
        content="住宿费标准",
        vector=[0, 1, 0, 0],
        seq=2,
        title="住宿",
    )
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))

    # 只用向量：向量分 = 余弦相似度，融合分即向量分。
    recall = await _recall(
        app, session, kb.id, "长江大桥", RetrievalConfig(mode="vector", top_k=10)
    )
    assert [c.chunk_id for c in recall.fusion] == [a, b]
    assert recall.fusion[0].vector_score == pytest.approx(1.0, abs=1e-6)
    assert recall.fusion[0].fusion_score == pytest.approx(1.0, abs=1e-6)
    assert recall.fulltext == []

    # 只用全文：按 BM25 排序，融合分即关键词分。
    recall = await _recall(
        app, session, kb.id, "长江大桥", RetrievalConfig(mode="fulltext", top_k=10)
    )
    assert recall.fulltext[0].chunk_id == a
    assert recall.fulltext[0].keyword_score == pytest.approx(1.0, abs=1e-6)
    assert recall.fulltext[0].fusion_score == pytest.approx(1.0, abs=1e-6)
    assert recall.vector == []

    # 混合：0.5 × 向量分 + 0.5 × 关键词分。
    recall = await _recall(
        app,
        session,
        kb.id,
        "长江大桥",
        RetrievalConfig(mode="hybrid", vector_weight=0.5, fulltext_weight=0.5, top_k=10),
    )
    scores = {c.chunk_id: c.fusion_score for c in recall.fusion}
    assert scores[a] == pytest.approx(0.5 * 1.0 + 0.5 * 1.0, abs=1e-6)
    assert scores[b] == pytest.approx(0.5 * 0.0 + 0.5 * 0.0, abs=1e-6)


# ---- 关键词分（12.2、X09 报告 6g）----


async def test_keyword_score_strpos_and_weights(app, session, monkeypatch) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    only_title = await _add_chunk(
        session,
        kb_id=kb.id,
        doc_id=doc,
        content="无关",
        vector=[1, 0, 0, 0],
        seq=1,
        title="报销制度",
    )
    only_body = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="报销标准", vector=[1, 0, 0, 0], seq=2
    )
    only_keywords = await _add_chunk(
        session,
        kb_id=kb.id,
        doc_id=doc,
        content="无关",
        vector=[1, 0, 0, 0],
        seq=3,
        keywords="报销",
    )
    none = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="无关", vector=[1, 0, 0, 0], seq=4
    )
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))

    recall = await _recall(app, session, kb.id, "报销", RetrievalConfig(mode="vector", top_k=10))
    scores = {c.chunk_id: c.keyword_score for c in recall.fusion}
    # title / body / keywords 任一命中都算，strpos 计算正确（X09 6g）。
    assert scores[only_title] == pytest.approx(1.0)
    assert scores[only_body] == pytest.approx(1.0)
    assert scores[only_keywords] == pytest.approx(1.0)
    assert scores[none] == pytest.approx(0.0)

    # 多词按权重求和：只命中一个词时分母是全部词权重。
    terms = await retrieval.build_query_terms(session, "报销 标准")
    assert terms.terms
    total = sum(terms.weights)
    matched = sum(
        weight for term, weight in zip(terms.terms, terms.weights, strict=True) if term in "报销"
    )
    partial = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="报销", vector=[1, 0, 0, 0], seq=5
    )
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))
    recall = await _recall(
        app, session, kb.id, "报销 标准", RetrievalConfig(mode="vector", top_k=10)
    )
    scores = {c.chunk_id: c.keyword_score for c in recall.fusion}
    assert scores[partial] == pytest.approx(matched / total, abs=1e-9)


# ---- 阈值与重排（12.2、12.3）----


async def test_threshold_vector_and_fulltext(app, session, monkeypatch) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    hit = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0], seq=1
    )
    await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="住宿标准", vector=[0, 1, 0, 0], seq=2
    )
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))

    # 未开启重排：阈值作用于融合分（向量模式即向量分）。
    config = RetrievalConfig(mode="vector", top_k=10, score_threshold=0.5)
    recall = await _recall(app, session, kb.id, "长江大桥", config)
    result = await retrieval.rerank_and_expand(
        app.state.sessionmaker, app.state.redis, question="长江大桥", recall=recall, config=config
    )
    assert [c.chunk_id for c in result.final] == [hit]

    # 只用全文：阈值作用于关键词分。
    config = RetrievalConfig(mode="fulltext", top_k=10, score_threshold=0.5)
    recall = await _recall(app, session, kb.id, "长江大桥", config)
    result = await retrieval.rerank_and_expand(
        app.state.sessionmaker, app.state.redis, question="长江大桥", recall=recall, config=config
    )
    assert [c.chunk_id for c in result.final] == [hit]


async def test_rerank_threshold_and_usage(app, session, monkeypatch) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    a = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0], seq=1
    )
    b = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="住宿标准", vector=[0, 1, 0, 0], seq=2
    )
    rerank_model = Model(
        name=f"重排模型-{uuid.uuid4().hex[:6]}",
        model_type="rerank",
        litellm_model="cohere/rerank-v3.5",
        created_by=user_id,
    )
    session.add(rerank_model)
    await session.commit()
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))
    # 融合顺序是 [a, b]，重排把 b 排到前面。
    monkeypatch.setattr(litellm, "arerank", fake_rerank([0.2, 0.9]))

    config = RetrievalConfig(
        mode="vector",
        top_k=10,
        score_threshold=0.5,
        rerank={"enabled": True, "model_id": rerank_model.id, "top_n": 2},
    )
    recall = await _recall(app, session, kb.id, "长江大桥", config)
    result = await retrieval.rerank_and_expand(
        app.state.sessionmaker, app.state.redis, question="长江大桥", recall=recall, config=config
    )
    assert [c.chunk_id for c in result.rerank] == [b, a]
    # 阈值作用于重排分数：只有 0.9 通过。
    assert [c.chunk_id for c in result.final] == [b]
    assert result.timings["rerank_ms"] >= 0

    usage = (
        (await session.execute(sa.select(ModelUsage).where(ModelUsage.source == "kb_hit_test")))
        .scalars()
        .all()
    )
    assert any(row.model_id == rerank_model.id for row in usage)
    assert any(row.model_id == kb.embedding_model_id for row in usage)


# ---- 过滤、停用与中文召回 ----


async def _folder_tree(session: AsyncSession, kb_id: int) -> tuple[KbFolder, KbFolder]:
    root = KbFolder(kb_id=kb_id, parent_id=None, path="/f1/", name="财务")
    session.add(root)
    await session.flush()
    child = KbFolder(kb_id=kb_id, parent_id=root.id, path=f"/f1/{root.id}/", name="子文件夹")
    session.add(child)
    await session.commit()
    return root, child


async def test_metadata_folder_and_active_filters(app, session, monkeypatch) -> None:
    kb, user_id = await _prepare_kb(session)
    field = KbMetadataField(kb_id=kb.id, name="部门", type="text", options=None)
    session.add(field)
    await session.commit()
    root, child = await _folder_tree(session, kb.id)

    doc_root = await _make_doc(
        session, kb_id=kb.id, user_id=user_id, folder_id=root.id, metadata={"部门": "财务"}
    )
    doc_child = await _make_doc(
        session, kb_id=kb.id, user_id=user_id, folder_id=child.id, metadata={"部门": "研发"}
    )
    doc_none = await _make_doc(session, kb_id=kb.id, user_id=user_id, metadata={"部门": "财务"})

    in_root = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc_root, content="长江大桥", vector=[1, 0, 0, 0], seq=1
    )
    in_child = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc_child, content="长江大桥", vector=[1, 0, 0, 0], seq=1
    )
    in_none = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc_none, content="长江大桥", vector=[1, 0, 0, 0], seq=1
    )
    # 停用的分块不进召回。
    await _add_chunk(
        session,
        kb_id=kb.id,
        doc_id=doc_root,
        content="长江大桥",
        vector=[1, 0, 0, 0],
        seq=2,
        active=False,
    )
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))
    config = RetrievalConfig(mode="vector", top_k=10)

    async def ids(**kwargs) -> set[int]:
        recall = await retrieval.recall_and_fuse(
            app.state.sessionmaker,
            app.state.redis,
            kb_ids=[kb.id],
            question="长江大桥",
            config=config,
            **kwargs,
        )
        return {c.chunk_id for c in recall.fusion}

    # 文件夹含下级。
    assert await ids(folder_id=root.id) == {in_root, in_child}
    # 元数据单条件。
    assert await ids(
        metadata=MetadataGroup(
            op="and", conditions=[MetadataFilter(field_id=field.id, operator="eq", value="财务")]
        )
    ) == {in_root, in_none}
    # 一层“且”：文件夹 + 元数据。
    assert await ids(
        folder_id=root.id,
        metadata=MetadataGroup(
            op="and", conditions=[MetadataFilter(field_id=field.id, operator="eq", value="财务")]
        ),
    ) == {in_root}
    # 一层“或”多条件。
    assert await ids(
        metadata=MetadataGroup(
            op="or",
            conditions=[
                MetadataFilter(field_id=field.id, operator="eq", value="财务"),
                MetadataFilter(field_id=field.id, operator="eq", value="研发"),
            ],
        )
    ) == {in_root, in_child, in_none}


async def test_disabled_kb_skipped_and_disabled_model(app, session, monkeypatch) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    await _add_chunk(session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0])
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))
    config = RetrievalConfig(mode="vector", top_k=10)

    # 知识库停用：跳过并返回空结果。
    resource = await session.get(Resource, kb.id)
    assert resource is not None
    resource.status = "disabled"
    await session.commit()
    recall = await _recall(app, session, kb.id, "长江大桥", config)
    assert recall.fusion == [] and recall.vector == [] and recall.fulltext == []

    # 向量化模型停用：返回“模型不可用”。
    resource.status = "enabled"
    model = await session.get(Model, kb.embedding_model_id)
    assert model is not None
    model.status = "disabled"
    await session.commit()
    with pytest.raises(EmbeddingModelUnavailableError):
        await _recall(app, session, kb.id, "长江大桥", config)


async def test_chinese_recall_long_word_and_title_boost(app, session, monkeypatch) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    title_hit = await _add_chunk(
        session,
        kb_id=kb.id,
        doc_id=doc,
        content="无关内容",
        vector=[1, 0, 0, 0],
        seq=1,
        title="长江大桥",
    )
    body_hit = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[0, 1, 0, 0], seq=2
    )
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))

    recall = await _recall(app, session, kb.id, "大桥", RetrievalConfig(mode="fulltext", top_k=10))
    ids = [c.chunk_id for c in recall.fulltext]
    # 搜“大桥”能召回含“长江大桥”的条目（搜索模式拆短词）。
    assert title_hit in ids and body_hit in ids
    # 标题 2 倍加权：标题命中的排在正文命中的前面。
    assert recall.fulltext[0].chunk_id == title_hit


async def test_keywords_boost(app, session, monkeypatch) -> None:
    """关键词字段同样 2 倍加权（11.4）。"""
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    keyword_hit = await _add_chunk(
        session,
        kb_id=kb.id,
        doc_id=doc,
        content="无关内容",
        vector=[1, 0, 0, 0],
        seq=1,
        keywords="长江大桥",
    )
    body_hit = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0], seq=2
    )
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))

    recall = await _recall(app, session, kb.id, "大桥", RetrievalConfig(mode="fulltext", top_k=10))
    ids = [c.chunk_id for c in recall.fulltext]
    assert keyword_hit in ids and body_hit in ids
    assert recall.fulltext[0].chunk_id == keyword_hit


# ---- P3-13 缺陷修复：typmod 缓存与预编译语句（2026-10-25 联调发现）----


async def test_query_terms_typmod_cache_survives_rollback(app) -> None:
    """typmod 行随事务回滚后，缓存的预编译语句再执行不能报错。

    复现联调时的 500：`build_query_terms` 的会话不提交，第一次解析
    `pdb.jieba(...)` 时 ParadeDB 写入 `paradedb._typmod_cache` 的类型修饰行会随事务回滚；
    而 asyncpg 缓存的预编译语句把类型修饰 id 固化在计划里，下一次执行查不到行就报
    “stored tokenizer options could not be found”。这里用一个没用过的 jieba 选项，
    走“解析 → 回滚 → 复用同一条预编译语句”的序列（与 `build_query_terms` 的 SQL 同形）。
    """
    option = f"alias=typmod_{uuid.uuid4().hex}"
    query = text(f"SELECT :q ::text::pdb.jieba('{option}')::text[] AS terms")

    async with app.state.engine.connect() as conn:
        # 第一次执行会解析并预编译，parse 时写入类型修饰行；回滚把行删掉。
        trans = await conn.begin()
        first = (await conn.execute(query, {"q": "长江大桥"})).all()
        await trans.rollback()

        # 同一个连接复用缓存的预编译语句，类型修饰行已经不在了。
        second = (await conn.execute(query, {"q": "长江大桥"})).all()

    assert first and first[0].terms
    assert second and second[0].terms


async def test_retrieval_after_other_kb_index_drop(app, session, monkeypatch) -> None:
    """删除别的知识库索引表后，本库检索仍可用（联调场景回归）。"""
    kb_a, user_a = await _prepare_kb(session, name="库A")
    kb_b, user_b = await _prepare_kb(session, name="库B")
    doc_a = await _make_doc(session, kb_id=kb_a.id, user_id=user_a)
    doc_b = await _make_doc(session, kb_id=kb_b.id, user_id=user_b)
    await _add_chunk(
        session, kb_id=kb_a.id, doc_id=doc_a, content="长江大桥", vector=[1, 0, 0, 0], title="差旅"
    )
    await _add_chunk(
        session, kb_id=kb_b.id, doc_id=doc_b, content="住宿费", vector=[1, 0, 0, 0], title="住宿"
    )
    monkeypatch.setattr(litellm, "aembedding", fake_embedding([1, 0, 0, 0]))
    config = RetrievalConfig(mode="fulltext", top_k=10)

    first = await _recall(app, session, kb_a.id, "长江大桥", config)
    assert first.fulltext

    await drop_index_table(session, kb_b.id)
    await session.commit()

    second = await _recall(app, session, kb_a.id, "长江大桥", config)
    assert second.fulltext
