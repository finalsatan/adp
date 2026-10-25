"""向量化、索引写入与两代切换（P3-11，知识库技术方案 8.1、8.4、9.4、11.3、11.4）。

连真实 PostgreSQL（ParadeDB 镜像）、Redis、RustFS；解析服务用 P3-09 的假服务，
向量化用固定向量的模型（monkeypatch `litellm.aembedding`）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest
from docling_core.types.doc.document import DoclingDocument
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ChunkTooLongError
from app.gateway.ratelimit import RateLimited
from app.gateway.tables import ModelUsage
from app.iam.tables import User
from app.kb import indexing, pipeline
from app.kb.chunking import chunk_document, write_chunks
from app.kb.index_table import create_index_table, index_table_name
from app.kb.schemas import SchemeConfig
from app.kb.tables import Chunk, Document, KnowledgeBase
from app.models.tables import Model
from app.worker.tokens import RetryLater
from tests.worker import make_document, make_kb

FIXTURES = Path(__file__).parent / "fixtures"


def load_doc(name: str = "pdf") -> DoclingDocument:
    return DoclingDocument.load_from_json(FIXTURES / f"{name}.json")


def make_scheme(file_type: str = "pdf", chunk: dict | None = None) -> SchemeConfig:
    return SchemeConfig.model_validate(
        {
            "schema_version": 1,
            "file_type": file_type,
            "parse": {"engine": "builtin"},
            "chunk": chunk or {"mode": "auto", "max_tokens": 500, "min_tokens": 10},
        }
    )


def fake_embedding(dim: int, calls: list[int]):
    """返回固定向量的假向量化；`calls` 记录每次请求的条数，供分批断言。"""

    async def _fake(**kwargs):
        inputs = kwargs["input"]
        calls.append(len(inputs))
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1] * dim) for _ in inputs],
            usage=SimpleNamespace(
                prompt_tokens=sum(len(item) for item in inputs), completion_tokens=0
            ),
        )

    return _fake


async def _chunk_rows(session: AsyncSession, doc_id: int, generation: int) -> list[Chunk]:
    result = await session.execute(
        select(Chunk)
        .where(Chunk.doc_id == doc_id, Chunk.generation == generation)
        .order_by(Chunk.seq)
    )
    return list(result.scalars().all())


async def _prepare_kb(
    session: AsyncSession,
    *,
    dim: int = 1024,
    batch_size: int = 2,
    max_input: int = 100000,
    name: str = "索引库",
) -> tuple[KnowledgeBase, int, int, Model]:
    kb, user_id = await make_kb(session, name, dim=dim)
    doc_id = await make_document(session, kb.id, user_id, name="制度.pdf")
    await create_index_table(session, kb.id, dim)
    model = await session.get(Model, kb.embedding_model_id)
    assert model is not None
    model.embedding_batch_size = batch_size
    model.embedding_max_input = max_input
    await session.commit()
    return kb, user_id, doc_id, model


# ---- 分批向量化与用量（8.1、9.4、17）----


async def test_embed_chunks_batches_and_usage(app, session: AsyncSession, monkeypatch) -> None:
    """每批条数等于 embedding_batch_size；用量记 ingest / 文档 / run_by 与组织。"""
    kb, user_id, doc_id, model = await _prepare_kb(session, batch_size=2)
    chunks = chunk_document(load_doc("pdf"), make_scheme())
    await write_chunks(
        session, kb_id=kb.id, doc_id=doc_id, generation=1, chunks=chunks, run_by=user_id
    )
    await session.commit()
    rows = await _chunk_rows(session, doc_id, 1)

    calls: list[int] = []
    monkeypatch.setattr(litellm, "aembedding", fake_embedding(1024, calls))

    await indexing.index_chunks(
        app.state.sessionmaker,
        app.state.redis,
        kb_id=kb.id,
        doc_id=doc_id,
        generation=1,
        run_by=user_id,
        org_id=(await session.get(User, user_id)).org_id,
        model=model,
        chunks=rows,
        title_path_enabled=True,
    )

    # 批大小 2：除最后一批外每批都是 2，最后一批不超过 2。
    assert sum(calls) == len(rows)
    assert calls[:-1] == [2] * (len(calls) - 1)
    assert all(size <= 2 for size in calls)
    usage = list(
        (await session.execute(select(ModelUsage).order_by(ModelUsage.id))).scalars().all()
    )
    assert len(usage) == len(calls)
    user = await session.get(User, user_id)
    for row in usage:
        assert row.source == "ingest"
        assert row.source_id == doc_id
        assert row.user_id == user_id
        assert row.org_id == user.org_id
        assert row.ok is True


# ---- 写索引表与全文召回（11.3、11.4）----


async def test_index_entries_active_and_fulltext_recall(
    app, session: AsyncSession, monkeypatch
) -> None:
    """写入 active=false 的 chunk 条目，维度与模型一致；切换后能按 BM25 召回。"""
    kb, user_id, doc_id, model = await _prepare_kb(session)
    chunks = chunk_document(load_doc("pdf"), make_scheme())
    await write_chunks(
        session, kb_id=kb.id, doc_id=doc_id, generation=1, chunks=chunks, run_by=user_id
    )
    await session.commit()
    rows = await _chunk_rows(session, doc_id, 1)

    monkeypatch.setattr(litellm, "aembedding", fake_embedding(1024, []))
    await indexing.index_chunks(
        app.state.sessionmaker,
        app.state.redis,
        kb_id=kb.id,
        doc_id=doc_id,
        generation=1,
        run_by=user_id,
        org_id=None,
        model=model,
        chunks=rows,
        title_path_enabled=True,
    )

    table = index_table_name(kb.id)
    stored = (
        await session.execute(
            text(f"SELECT count(*), bool_and(active) FROM {table} WHERE entry_type = 'chunk'")
        )
    ).one()
    assert stored[0] == len(rows)
    assert stored[1] is False  # 生效前全部 active=false

    dims = await session.scalar(text(f"SELECT vector_dims(embedding) FROM {table} LIMIT 1"))
    assert dims == 1024

    # 生效后按 X09 的写法全文召回（title / body 命中，pdb.score 排序）。
    await indexing.activate_generation(
        session,
        kb_id=kb.id,
        doc_id=doc_id,
        generation=1,
        chunk_count=len(rows),
        enabled=True,
    )
    await session.commit()

    result = await session.execute(
        text(
            f"SELECT chunk_id, body, pdb.score(id) AS score FROM {table} "
            "WHERE (" + indexing.FULLTEXT_CONDITION + ") AND active "
            "ORDER BY pdb.score(id) DESC LIMIT 5"
        ),
        {"q": "报销"},
    )
    hits = result.all()
    assert hits, "全文召回没有结果"
    assert any("报销" in row.body for row in hits)


async def test_halfvec_dimension_2560(app, session: AsyncSession, monkeypatch) -> None:
    """维度 2560 的模型用 halfvec，HNSW 用 halfvec_cosine_ops（P3-03 建表）。"""
    kb, user_id, doc_id, model = await _prepare_kb(session, dim=2560, batch_size=1)
    chunks = chunk_document(load_doc("text"), make_scheme("txt", {"mode": "auto"}))
    await write_chunks(
        session, kb_id=kb.id, doc_id=doc_id, generation=1, chunks=chunks, run_by=user_id
    )
    await session.commit()
    rows = await _chunk_rows(session, doc_id, 1)

    monkeypatch.setattr(litellm, "aembedding", fake_embedding(2560, []))
    await indexing.index_chunks(
        app.state.sessionmaker,
        app.state.redis,
        kb_id=kb.id,
        doc_id=doc_id,
        generation=1,
        run_by=user_id,
        org_id=None,
        model=model,
        chunks=rows,
        title_path_enabled=True,
    )

    table = index_table_name(kb.id)
    embedding_type = await session.scalar(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = to_regclass(:t) AND attname = 'embedding'"
        ),
        {"t": table},
    )
    assert embedding_type == "halfvec(2560)"
    defs = [
        row[0]
        for row in await session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'kb_index' AND tablename = :n"
            ),
            {"n": f"kb_{kb.id}"},
        )
    ]
    hnsw = [item for item in defs if "USING hnsw" in item]
    assert len(hnsw) == 1 and "halfvec_cosine_ops" in hnsw[0]


# ---- 两代切换（8.4、需求 4.1.5）----


async def test_two_generation_switch(app, session: AsyncSession, monkeypatch) -> None:
    """新一代写入后切换前旧一代仍可检索；切换提交后新一代生效、计数更新。"""
    kb, user_id, doc_id, model = await _prepare_kb(session, batch_size=10)
    monkeypatch.setattr(litellm, "aembedding", fake_embedding(1024, []))

    async def index_generation(generation: int, chunk_config: dict) -> int:
        chunks = chunk_document(load_doc("pdf"), make_scheme("pdf", chunk_config))
        await write_chunks(
            session,
            kb_id=kb.id,
            doc_id=doc_id,
            generation=generation,
            chunks=chunks,
            run_by=user_id,
        )
        await session.commit()
        rows = await _chunk_rows(session, doc_id, generation)
        await indexing.index_chunks(
            app.state.sessionmaker,
            app.state.redis,
            kb_id=kb.id,
            doc_id=doc_id,
            generation=generation,
            run_by=user_id,
            org_id=None,
            model=model,
            chunks=rows,
            title_path_enabled=True,
        )
        return len(rows)

    first_count = await index_generation(1, {"mode": "whole", "max_tokens": 5000})
    await indexing.activate_generation(
        session,
        kb_id=kb.id,
        doc_id=doc_id,
        generation=1,
        chunk_count=first_count,
        enabled=True,
    )
    await session.commit()

    # 新一代（generation=2）写入但不生效。
    await index_generation(2, {"mode": "auto", "max_tokens": 60, "min_tokens": 10})
    table = index_table_name(kb.id)
    active = (
        await session.execute(
            text(f"SELECT generation, count(*) FROM {table} WHERE active GROUP BY generation")
        )
    ).all()
    assert active == [(1, first_count)], "切换前只有旧一代 active"
    assert (
        await session.scalar(text(f"SELECT count(*) FROM {table} WHERE generation = 2 AND active"))
        == 0
    )

    # 旧一代仍能被检索到（需求 4.1.5）。
    hits = await session.execute(
        text(
            f"SELECT chunk_id FROM {table} WHERE (" + indexing.FULLTEXT_CONDITION + ") "
            "AND active ORDER BY pdb.score(id) DESC LIMIT 5"
        ),
        {"q": "报销"},
    )
    assert hits.all()

    # 切换：旧一代 active=false、新一代 active=true、current_generation 与计数更新。
    second_count = len(await _chunk_rows(session, doc_id, 2))
    await indexing.activate_generation(
        session,
        kb_id=kb.id,
        doc_id=doc_id,
        generation=2,
        chunk_count=second_count,
        enabled=True,
    )
    await session.commit()

    doc = await session.get(Document, doc_id)
    assert doc is not None
    assert doc.current_generation == 2
    assert doc.chunk_count == second_count
    kb_row = await session.get(KnowledgeBase, kb.id)
    assert kb_row is not None
    await session.refresh(kb_row)
    assert kb_row.doc_count == 1
    assert kb_row.chunk_count == second_count
    assert (
        await session.scalar(text(f"SELECT count(*) FROM {table} WHERE generation = 1 AND active"))
        == 0
    )
    assert (
        await session.scalar(text(f"SELECT count(*) FROM {table} WHERE generation = 2 AND active"))
        == second_count
    )


async def test_set_doc_active(app, session: AsyncSession, monkeypatch) -> None:
    """停用文档把所有代的条目置 false，启用只把当前代置 true。"""
    kb, user_id, doc_id, model = await _prepare_kb(session, batch_size=10)
    monkeypatch.setattr(litellm, "aembedding", fake_embedding(1024, []))
    for generation in (1, 2):
        chunks = chunk_document(load_doc("pdf"), make_scheme("pdf", {"mode": "whole"}))
        await write_chunks(
            session,
            kb_id=kb.id,
            doc_id=doc_id,
            generation=generation,
            chunks=chunks,
            run_by=user_id,
        )
        await session.commit()
        rows = await _chunk_rows(session, doc_id, generation)
        await indexing.index_chunks(
            app.state.sessionmaker,
            app.state.redis,
            kb_id=kb.id,
            doc_id=doc_id,
            generation=generation,
            run_by=user_id,
            org_id=None,
            model=model,
            chunks=rows,
            title_path_enabled=True,
        )
    await indexing.activate_generation(
        session, kb_id=kb.id, doc_id=doc_id, generation=2, chunk_count=1, enabled=True
    )
    await session.commit()
    table = index_table_name(kb.id)

    await indexing.set_doc_active(session, kb.id, doc_id, False)
    await session.commit()
    assert await session.scalar(text(f"SELECT count(*) FROM {table} WHERE active")) == 0

    await indexing.set_doc_active(session, kb.id, doc_id, True)
    await session.commit()
    active_generations = (
        (await session.execute(text(f"SELECT DISTINCT generation FROM {table} WHERE active")))
        .scalars()
        .all()
    )
    assert active_generations == [2]


# ---- 整条链路与失败处理 ----


async def _run(app, parser_client, context) -> None:
    await pipeline.run_pipeline(
        app.state.sessionmaker,
        app.state.redis,
        context.doc_id,
        context.run_id,
        settings=app.state.settings,
        storage=app.state.storage,
        parser_client=parser_client,
    )


async def _configure_model(app, kb_id: int, **fields) -> None:
    async with app.state.sessionmaker() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        assert kb is not None
        model = await session.get(Model, kb.embedding_model_id)
        assert model is not None
        for key, value in fields.items():
            setattr(model, key, value)
        await session.commit()


async def test_pipeline_parse_chunk_embed_activate(
    app, parser_client, make_ingest_document, monkeypatch
) -> None:
    """解析 → 分块 → 向量化 → 生效整条链路。"""
    context = await make_ingest_document()
    async with app.state.sessionmaker() as session:
        await create_index_table(session, context.kb_id, 1024)
        await session.commit()
    await _configure_model(app, context.kb_id, embedding_batch_size=2, embedding_max_input=100000)
    calls: list[int] = []
    monkeypatch.setattr(litellm, "aembedding", fake_embedding(1024, calls))

    await _run(app, parser_client, context)

    async with app.state.sessionmaker() as session:
        doc = await session.get(Document, context.doc_id)
        assert doc is not None
        assert doc.status == "done"
        assert doc.step == pipeline.STEP_ACTIVATE
        assert doc.failed_step is None
        assert doc.current_generation == 1
        assert doc.chunk_count > 0
        assert len(calls) >= 1
        assert calls[0] == 2

        rows = await _chunk_rows(session, context.doc_id, 1)
        assert len(rows) == doc.chunk_count
        table = index_table_name(context.kb_id)
        assert (
            await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE active AND entry_type = 'chunk'")
            )
            == doc.chunk_count
        )
        kb = await session.get(KnowledgeBase, context.kb_id)
        assert kb is not None
        assert kb.doc_count == 1
        assert kb.chunk_count == doc.chunk_count


async def test_pipeline_rate_limited_retries_later(
    app, parser_client, make_ingest_document, monkeypatch
) -> None:
    """入库额度限流：抛 RetryLater，文档不失败（8.3）。"""
    context = await make_ingest_document()
    async with app.state.sessionmaker() as session:
        await create_index_table(session, context.kb_id, 1024)
        await session.commit()
    await _configure_model(app, context.kb_id, embedding_batch_size=2)

    async def limited(*args, **kwargs):
        raise RateLimited("ingest")

    monkeypatch.setattr(indexing.gateway, "embed", limited)

    with pytest.raises(RetryLater):
        await _run(app, parser_client, context)

    async with app.state.sessionmaker() as session:
        doc = await session.get(Document, context.doc_id)
        assert doc is not None
        assert doc.status != "failed"
        assert doc.failed_step is None


async def test_pipeline_overlong_fails_at_chunk_step(
    app, parser_client, make_ingest_document, monkeypatch
) -> None:
    """超长（>90%）时分块失败，error_message 指向分块序号和页码（9.4）。"""
    context = await make_ingest_document()
    async with app.state.sessionmaker() as session:
        await create_index_table(session, context.kb_id, 1024)
        await session.commit()
    await _configure_model(app, context.kb_id, embedding_batch_size=2, embedding_max_input=5)
    monkeypatch.setattr(litellm, "aembedding", fake_embedding(1024, []))

    await _run(app, parser_client, context)

    async with app.state.sessionmaker() as session:
        doc = await session.get(Document, context.doc_id)
        assert doc is not None
        assert doc.status == "failed"
        assert doc.failed_step == pipeline.STEP_CHUNK
        assert doc.error_code == ChunkTooLongError.code
        assert "第" in (doc.error_message or "")
        assert "页" in (doc.error_message or "")
