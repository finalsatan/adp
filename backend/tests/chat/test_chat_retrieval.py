"""回答流程中的文档库检索测试（P4-07）。

连真实 PostgreSQL（ParadeDB 镜像）、Redis；向量与重排模型用固定结果的假实现
（monkeypatch `litellm.aembedding` / `litellm.arerank`），数据集固定、向量维度小。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import litellm
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import (
    AppDocConfig,
    AppIntentConfig,
    AppKbFilter,
    AppKbRef,
    AppKnowledgeConfig,
    AppRerankConfig,
    AppRewriteConfig,
    KbQaAppConfig,
)
from app.chat.pipeline import AnswerRun, run_flow
from app.chat.retrieval import retrieve_documents
from app.chat.schemas import AnswerKind, StepName
from app.core.errors import RetrievalFailedError
from app.core.schemas import Role
from app.gateway.tables import ModelUsage
from app.kb import retrieval as kb_retrieval
from app.kb.index_table import create_index_table, index_table_name
from app.kb.schemas import (
    MetadataFilter,
    MetadataGroup,
    RetrievalConfig,
)
from app.kb.tables import Chunk, Document, KbFolder, KbMetadataField, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from tests.chat.conftest import (
    Emitter,
    build_config,
    make_chat_model,
    make_rerank_model,
)
from tests.factories import make_org, make_user
from tests.worker import make_kb


class FakeEmbedding:
    """固定向量的假向量化；记录每次调用，可按 `litellm_model` 指定失败。"""

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [1, 0, 0, 0]
        self.calls: list[dict] = []
        self.fail_models: set[str] = set()

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["model"] in self.fail_models:
            raise RuntimeError("embedding failed")
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=list(self.vector)) for _ in kwargs["input"]],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0),
        )


class FakeRerank:
    """按输入顺序返回递减相关度的假重排；记录每次调用。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail = False

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("rerank failed")
        documents = kwargs["documents"]
        top_n = kwargs.get("top_n") or len(documents)
        results = [
            SimpleNamespace(index=index, relevance_score=1.0 - index * 0.1)
            for index in range(len(documents))
        ]
        return SimpleNamespace(results=results[:top_n])


@pytest.fixture
def fake_embedding(monkeypatch) -> FakeEmbedding:
    fake = FakeEmbedding()
    monkeypatch.setattr(litellm, "aembedding", fake)
    return fake


@pytest.fixture
def fake_rerank(monkeypatch) -> FakeRerank:
    fake = FakeRerank()
    monkeypatch.setattr(litellm, "arerank", fake)
    return fake


async def _make_kb_with_model(
    session: AsyncSession, name: str, model: Model, dim: int = 4
) -> tuple[KnowledgeBase, int]:
    org = await make_org(session, f"{name}组织")
    user = await make_user(session, role=Role.builder, org=org)
    resource = Resource(type="kb", name=name, org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind="doc",
        embedding_model_id=model.id,
        embedding_dim=dim,
        index_table=f"kb_index.kb_{resource.id}",
    )
    session.add(kb)
    await session.commit()
    return kb, user.id


async def _prepare_kb(
    session: AsyncSession,
    name: str = "检索库",
    dim: int = 4,
    model: Model | None = None,
) -> tuple[KnowledgeBase, int]:
    if model is None:
        kb, user_id = await make_kb(session, name, dim=dim)
    else:
        kb, user_id = await _make_kb_with_model(session, name, model, dim)
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
    generation: int = 1,
) -> int:
    chunk = Chunk(
        kb_id=kb_id,
        doc_id=doc_id,
        generation=generation,
        seq=seq,
        kind="normal",
        title_path=title,
        content=content,
        positions=None,
        source="auto",
        edited=False,
    )
    session.add(chunk)
    await session.flush()
    literal = "[" + ",".join(str(float(value)) for value in vector) + "]"
    await session.execute(
        sa.text(
            f"INSERT INTO {index_table_name(kb_id)} "
            "(entry_type, chunk_id, doc_id, generation, active, embedding, title, body, keywords) "
            "VALUES ('chunk', :cid, :did, :gen, true, :emb, :title, :body, NULL)"
        ),
        {
            "cid": chunk.id,
            "did": doc_id,
            "gen": generation,
            "emb": literal,
            "title": title,
            "body": content,
        },
    )
    await session.commit()
    return chunk.id


def _config(kbs: list[AppKbRef], doc: AppDocConfig | None = None) -> KbQaAppConfig:
    return KbQaAppConfig(knowledge=AppKnowledgeConfig(kbs=kbs, doc=doc or AppDocConfig()))


def _run(
    app,
    config: KbQaAppConfig,
    *,
    question: str = "长江大桥",
    query_text: str | None = None,
    app_id: int = 7,
) -> AnswerRun:
    return AnswerRun(
        app_id=app_id,
        config=config,
        question=question,
        query_text=query_text,
        sessionmaker=app.state.sessionmaker,
        redis=app.state.redis,
        user_id=1,
        org_id=1,
    )


# ---- 向量化缓存：同一模型一次提问只调一次 ----


async def test_same_embedding_model_called_once_across_kbs(app, session, fake_embedding) -> None:
    kb1, user1 = await _prepare_kb(session, "库一")
    model = await session.get(Model, kb1.embedding_model_id)
    assert model is not None
    kb2, user2 = await _prepare_kb(session, "库二", model=model)
    doc1 = await _make_doc(session, kb_id=kb1.id, user_id=user1)
    doc2 = await _make_doc(session, kb_id=kb2.id, user_id=user2)
    await _add_chunk(session, kb_id=kb1.id, doc_id=doc1, content="长江大桥", vector=[1, 0, 0, 0])
    await _add_chunk(session, kb_id=kb2.id, doc_id=doc2, content="长江大桥", vector=[1, 0, 0, 0])
    config = _config(
        [
            AppKbRef(kb_id=kb1.id, retrieval=RetrievalConfig(mode="vector", top_k=5)),
            AppKbRef(kb_id=kb2.id, retrieval=RetrievalConfig(mode="vector", top_k=5)),
        ]
    )

    outcome = await retrieve_documents(_run(app, config), Emitter())

    assert len(fake_embedding.calls) == 1
    assert {candidate.kb_id for candidate in outcome.candidates} == {kb1.id, kb2.id}


async def test_query_text_used_for_embedding(app, session, fake_embedding) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    await _add_chunk(session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0])
    config = _config([AppKbRef(kb_id=kb.id, retrieval=RetrievalConfig(mode="vector", top_k=5))])

    await retrieve_documents(
        _run(app, config, question="原问题", query_text="改写后的问题"), Emitter()
    )

    assert fake_embedding.calls[0]["input"] == ["改写后的问题"]


# ---- 重排：每库各取前 top_k，合并一次重排，保留 keep ----


async def test_mixed_embedding_models_rerank_per_kb_topk(
    app, session, fake_embedding, fake_rerank
) -> None:
    kb1, user1 = await _prepare_kb(session, "库一")
    kb2, user2 = await _prepare_kb(session, "库二")
    assert kb1.embedding_model_id != kb2.embedding_model_id
    doc1 = await _make_doc(session, kb_id=kb1.id, user_id=user1)
    doc2 = await _make_doc(session, kb_id=kb2.id, user_id=user2)
    await _add_chunk(
        session, kb_id=kb1.id, doc_id=doc1, content="长江大桥", vector=[1, 0, 0, 0], seq=1
    )
    await _add_chunk(
        session,
        kb_id=kb1.id,
        doc_id=doc1,
        content="无关内容",
        vector=[0, 1, 0, 0],
        seq=2,
        title="长江大桥",
    )
    await _add_chunk(
        session, kb_id=kb2.id, doc_id=doc2, content="长江大桥", vector=[1, 0, 0, 0], seq=1
    )
    await _add_chunk(
        session,
        kb_id=kb2.id,
        doc_id=doc2,
        content="无关内容",
        vector=[0, 1, 0, 0],
        seq=2,
        title="长江大桥",
    )
    rerank_model = await make_rerank_model(session, "重排模型")
    config = _config(
        [
            AppKbRef(
                kb_id=kb1.id,
                retrieval=RetrievalConfig(
                    mode="hybrid", vector_weight=0.5, fulltext_weight=0.5, top_k=5
                ),
            ),
            AppKbRef(
                kb_id=kb2.id,
                retrieval=RetrievalConfig(
                    mode="hybrid", vector_weight=0.5, fulltext_weight=0.5, top_k=5
                ),
            ),
        ],
        doc=AppDocConfig(
            top_k=1,
            rerank=AppRerankConfig(enabled=True, model_id=rerank_model.id, keep=1),
        ),
    )

    outcome = await retrieve_documents(_run(app, config), Emitter())

    assert len(fake_embedding.calls) == 2
    assert len(fake_rerank.calls) == 1
    # 混合检索下每个库的融合结果有 2 条，但每库只取融合分前 1 条，共 2 条交给重排。
    assert len(fake_rerank.calls[0]["documents"]) == 2
    # keep=1。
    assert len(outcome.candidates) == 1


async def test_no_rerank_merges_by_fusion_score(app, session, fake_embedding) -> None:
    kb1, user1 = await _prepare_kb(session, "库一")
    model = await session.get(Model, kb1.embedding_model_id)
    assert model is not None
    kb2, user2 = await _prepare_kb(session, "库二", model=model)
    doc1 = await _make_doc(session, kb_id=kb1.id, user_id=user1)
    doc2 = await _make_doc(session, kb_id=kb2.id, user_id=user2)
    hit = await _add_chunk(
        session, kb_id=kb1.id, doc_id=doc1, content="长江大桥", vector=[1, 0, 0, 0]
    )
    mid = await _add_chunk(
        session, kb_id=kb2.id, doc_id=doc2, content="长江大桥", vector=[0.6, 0.8, 0, 0]
    )
    await _add_chunk(
        session, kb_id=kb2.id, doc_id=doc2, content="住宿标准", vector=[0, 1, 0, 0], seq=2
    )
    config = _config(
        [
            AppKbRef(kb_id=kb1.id, retrieval=RetrievalConfig(mode="vector", top_k=5)),
            AppKbRef(kb_id=kb2.id, retrieval=RetrievalConfig(mode="vector", top_k=5)),
        ],
        doc=AppDocConfig(top_k=2, score_threshold=0.5),
    )

    outcome = await retrieve_documents(_run(app, config), Emitter())

    assert [candidate.chunk_ids[0] for candidate in outcome.candidates] == [hit, mid]
    assert all(candidate.score >= 0.5 for candidate in outcome.candidates)


# ---- 按库覆盖检索方式；覆盖为 null 用知识库默认 ----


async def test_kb_retrieval_mode_override_and_default(app, session, fake_embedding) -> None:
    kb, user_id = await _prepare_kb(session, "默认全文库")
    await session.execute(
        sa.update(KnowledgeBase)
        .where(KnowledgeBase.id == kb.id)
        .values(retrieval_config={"mode": "fulltext", "top_k": 5})
    )
    await session.commit()
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    chunk = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0]
    )

    # 应用覆盖为向量：调用向量化。
    override = _config([AppKbRef(kb_id=kb.id, retrieval=RetrievalConfig(mode="vector", top_k=5))])
    outcome = await retrieve_documents(_run(app, override), Emitter())
    assert len(fake_embedding.calls) == 1
    assert [candidate.chunk_ids for candidate in outcome.candidates] == [[chunk]]

    # 覆盖为 null：用知识库默认的全文，不调用向量化。
    calls_after_override = len(fake_embedding.calls)
    default = _config([AppKbRef(kb_id=kb.id, retrieval=None)])
    outcome = await retrieve_documents(_run(app, default), Emitter())
    assert len(fake_embedding.calls) == calls_after_override
    assert [candidate.chunk_ids for candidate in outcome.candidates] == [[chunk]]


async def test_kb_hybrid_weight_override(app, session, fake_embedding) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    # 一条向量命中（关键词分 0），一条关键词命中（向量分 0）。
    vector_hit = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="无关内容", vector=[1, 0, 0, 0], seq=1
    )
    keyword_hit = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[0, 1, 0, 0], seq=2
    )

    vector_heavy = _config(
        [
            AppKbRef(
                kb_id=kb.id,
                retrieval=RetrievalConfig(
                    mode="hybrid", vector_weight=0.9, fulltext_weight=0.1, top_k=5
                ),
            )
        ]
    )
    outcome = await retrieve_documents(_run(app, vector_heavy), Emitter())
    assert outcome.candidates[0].chunk_ids == [vector_hit]

    fulltext_heavy = _config(
        [
            AppKbRef(
                kb_id=kb.id,
                retrieval=RetrievalConfig(
                    mode="hybrid", vector_weight=0.1, fulltext_weight=0.9, top_k=5
                ),
            )
        ]
    )
    outcome = await retrieve_documents(_run(app, fulltext_heavy), Emitter())
    assert outcome.candidates[0].chunk_ids == [keyword_hit]


# ---- 过滤：文件夹（含下级）、元数据一层“且/或” ----


async def _folder_tree(session: AsyncSession, kb_id: int) -> tuple[KbFolder, KbFolder]:
    root = KbFolder(kb_id=kb_id, parent_id=None, path="/f1/", name="财务")
    session.add(root)
    await session.flush()
    child = KbFolder(kb_id=kb_id, parent_id=root.id, path=f"/f1/{root.id}/", name="子文件夹")
    session.add(child)
    await session.commit()
    return root, child


async def test_folder_and_metadata_filter(app, session, fake_embedding) -> None:
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
        session, kb_id=kb.id, doc_id=doc_root, content="长江大桥", vector=[1, 0, 0, 0]
    )
    in_child = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc_child, content="长江大桥", vector=[1, 0, 0, 0]
    )
    in_none = await _add_chunk(
        session, kb_id=kb.id, doc_id=doc_none, content="长江大桥", vector=[1, 0, 0, 0]
    )
    retrieval_cfg = RetrievalConfig(mode="vector", top_k=10)

    folder = _config(
        [
            AppKbRef(
                kb_id=kb.id,
                retrieval=retrieval_cfg,
                filter=AppKbFilter(folder_ids=[root.id]),
            )
        ]
    )
    outcome = await retrieve_documents(_run(app, folder), Emitter())
    assert {candidate.chunk_ids[0] for candidate in outcome.candidates} == {
        in_root,
        in_child,
    }

    metadata = _config(
        [
            AppKbRef(
                kb_id=kb.id,
                retrieval=retrieval_cfg,
                filter=AppKbFilter(
                    metadata=MetadataGroup(
                        op="and",
                        conditions=[MetadataFilter(field_id=field.id, operator="eq", value="财务")],
                    )
                ),
            )
        ]
    )
    outcome = await retrieve_documents(_run(app, metadata), Emitter())
    assert {candidate.chunk_ids[0] for candidate in outcome.candidates} == {
        in_root,
        in_none,
    }

    # 一层“或”多条件。
    either = _config(
        [
            AppKbRef(
                kb_id=kb.id,
                retrieval=retrieval_cfg,
                filter=AppKbFilter(
                    metadata=MetadataGroup(
                        op="or",
                        conditions=[
                            MetadataFilter(field_id=field.id, operator="eq", value="财务"),
                            MetadataFilter(field_id=field.id, operator="eq", value="研发"),
                        ],
                    )
                ),
            )
        ]
    )
    outcome = await retrieve_documents(_run(app, either), Emitter())
    assert {candidate.chunk_ids[0] for candidate in outcome.candidates} == {
        in_root,
        in_child,
        in_none,
    }


# ---- 停用与问答库跳过；全部跳过返回空候选 ----


async def test_disabled_and_qa_kbs_skipped(app, session, fake_embedding) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    await _add_chunk(session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0])

    # 停用：跳过，返回空候选，不调用模型。
    resource = await session.get(Resource, kb.id)
    assert resource is not None
    resource.status = "disabled"
    await session.commit()
    config = _config([AppKbRef(kb_id=kb.id)])
    outcome = await retrieve_documents(_run(app, config), Emitter())
    assert outcome.candidates == []
    assert outcome.per_kb == []
    assert fake_embedding.calls == []

    # 问答库（kind=qa）：第六期，跳过。
    resource.status = "enabled"
    await session.execute(
        sa.update(KnowledgeBase).where(KnowledgeBase.id == kb.id).values(kind="qa")
    )
    await session.commit()
    outcome = await retrieve_documents(_run(app, config), Emitter())
    assert outcome.candidates == []
    assert fake_embedding.calls == []


# ---- 降级：单库失败跳过、全部失败与重排失败抛 RETRIEVAL_FAILED ----


async def test_one_kb_failure_skipped_and_all_fail(app, session, fake_embedding) -> None:
    kb1, user1 = await _prepare_kb(session, "库一")
    kb2, user2 = await _prepare_kb(session, "库二")
    model1 = await session.get(Model, kb1.embedding_model_id)
    model2 = await session.get(Model, kb2.embedding_model_id)
    assert model1 is not None and model2 is not None
    model1.litellm_model = "openai/embed-a"
    model2.litellm_model = "openai/embed-b"
    await session.commit()
    doc1 = await _make_doc(session, kb_id=kb1.id, user_id=user1)
    doc2 = await _make_doc(session, kb_id=kb2.id, user_id=user2)
    await _add_chunk(session, kb_id=kb1.id, doc_id=doc1, content="长江大桥", vector=[1, 0, 0, 0])
    await _add_chunk(session, kb_id=kb2.id, doc_id=doc2, content="长江大桥", vector=[1, 0, 0, 0])
    config = _config(
        [
            AppKbRef(kb_id=kb1.id, retrieval=RetrievalConfig(mode="vector", top_k=5)),
            AppKbRef(kb_id=kb2.id, retrieval=RetrievalConfig(mode="vector", top_k=5)),
        ]
    )

    fake_embedding.fail_models = {"openai/embed-a"}
    outcome = await retrieve_documents(_run(app, config), Emitter())
    assert [candidate.kb_id for candidate in outcome.candidates] == [kb2.id]
    assert any(info.ok is False and info.kb_id == kb1.id for info in outcome.per_kb)

    fake_embedding.fail_models = {"openai/embed-a", "openai/embed-b"}
    with pytest.raises(RetrievalFailedError):
        await retrieve_documents(_run(app, config), Emitter())


async def test_rerank_failure_raises(app, session, fake_embedding, fake_rerank) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    await _add_chunk(session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0])
    rerank_model = await make_rerank_model(session, "重排模型")
    config = _config(
        [AppKbRef(kb_id=kb.id, retrieval=RetrievalConfig(mode="vector", top_k=5))],
        doc=AppDocConfig(
            top_k=5,
            rerank=AppRerankConfig(enabled=True, model_id=rerank_model.id, keep=1),
        ),
    )
    fake_rerank.fail = True
    with pytest.raises(RetrievalFailedError):
        await retrieve_documents(_run(app, config), Emitter())


# ---- 用量来源 ----


async def test_usage_source_is_app_and_hit_test_unchanged(
    app, session, fake_embedding, fake_rerank
) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    await _add_chunk(session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0])
    rerank_model = await make_rerank_model(session, "重排模型")
    config = _config(
        [AppKbRef(kb_id=kb.id, retrieval=RetrievalConfig(mode="vector", top_k=5))],
        doc=AppDocConfig(
            top_k=5,
            rerank=AppRerankConfig(enabled=True, model_id=rerank_model.id, keep=1),
        ),
    )

    await retrieve_documents(_run(app, config, app_id=42), Emitter())

    usage = (
        (await session.execute(sa.select(ModelUsage).where(ModelUsage.source == "app")))
        .scalars()
        .all()
    )
    assert any(row.model_id == kb.embedding_model_id and row.source_id == 42 for row in usage)
    assert any(row.model_id == rerank_model.id and row.source_id == 42 for row in usage)

    # 命中测试不传 source，仍记 kb_hit_test。
    await kb_retrieval.recall_and_fuse(
        app.state.sessionmaker,
        app.state.redis,
        kb_ids=[kb.id],
        question="长江大桥",
        config=RetrievalConfig(mode="vector", top_k=5),
    )
    hit = (
        (await session.execute(sa.select(ModelUsage).where(ModelUsage.source == "kb_hit_test")))
        .scalars()
        .all()
    )
    assert any(row.model_id == kb.embedding_model_id for row in hit)


# ---- 处理过程 ----


async def test_trace_records_recall_and_rerank(app, session, fake_embedding, fake_rerank) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    await _add_chunk(session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0])
    rerank_model = await make_rerank_model(session, "重排模型")
    config = _config(
        [AppKbRef(kb_id=kb.id, retrieval=RetrievalConfig(mode="vector", top_k=5))],
        doc=AppDocConfig(
            top_k=5,
            rerank=AppRerankConfig(enabled=True, model_id=rerank_model.id, keep=1),
        ),
    )
    run = _run(app, config)

    outcome = await retrieve_documents(run, Emitter())

    steps = {step.name: step for step in run.trace.build().steps}
    assert set(steps) == {StepName.retrieve, StepName.rerank}
    retrieve = steps[StepName.retrieve]
    assert retrieve.output["count"] >= 1
    assert retrieve.output["kbs"][0]["kb_id"] == kb.id
    assert retrieve.output["kbs"][0]["vector"] >= 1
    assert retrieve.output["timings"]["vector_ms"] >= 0
    assert retrieve.output["hits"][0]["content"]
    rerank = steps[StepName.rerank]
    assert rerank.model_id == rerank_model.id
    assert rerank.output["kept"] == len(outcome.candidates)


# ---- 与回答流程编排串联 ----


async def test_run_flow_uses_document_retrieval(app, session, fake_llm, fake_embedding) -> None:
    kb, user_id = await _prepare_kb(session)
    doc = await _make_doc(session, kb_id=kb.id, user_id=user_id)
    await _add_chunk(session, kb_id=kb.id, doc_id=doc, content="长江大桥", vector=[1, 0, 0, 0])
    model = await make_chat_model(session)
    config = build_config(
        model.id,
        intent=AppIntentConfig(enabled=False),
        rewrite=AppRewriteConfig(enabled=False),
        knowledge=AppKnowledgeConfig(
            kbs=[AppKbRef(kb_id=kb.id, retrieval=RetrievalConfig(mode="vector", top_k=5))]
        ),
    )
    run = AnswerRun(
        app_id=1,
        config=config,
        question="长江大桥",
        sessionmaker=app.state.sessionmaker,
        redis=app.state.redis,
    )

    await run_flow(run, Emitter())

    assert run.candidates
    assert run.kind == AnswerKind.generated
