"""调试预览测试（P4-11）。

连真实 PostgreSQL、Redis、RustFS；回答模型用 `tests/chat/conftest.py` 的假模型，
向量化 / 重排用固定结果的假实现（与 P4-07 的测试一致）。覆盖调试 SSE、TRACE、
草稿版本冲突、配置不完整、不落库与用量来源，以及调试分块预览的权限与整份 / 片段。
"""

from __future__ import annotations

import io
import json
import uuid
from types import SimpleNamespace

import litellm
import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import (
    AppDocConfig,
    AppIntentConfig,
    AppKbRef,
    AppKnowledgeConfig,
    AppRerankConfig,
    AppRewriteConfig,
    KbQaAppConfig,
)
from app.apps.tables import App
from app.chat import pipeline
from app.chat.retrieval import AnswerCandidate
from app.chat.schemas import StepName
from app.chat.tables import AnswerTrace, ChatAnswer, ChatTurn, Conversation
from app.core.schemas import Role
from app.core.storage import kb_original_key
from app.gateway.tables import ModelUsage
from app.kb.index_table import create_index_table, index_table_name
from app.kb.schemas import FileType, RetrievalConfig
from app.kb.tables import Chunk, Document, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource, ResourceMember
from tests.chat.conftest import build_config, make_chat_model, make_rerank_model
from tests.factories import auth_cookies, make_org, make_user
from tests.worker import make_kb

CHAT_URL = "/api/v1/apps/{app_id}/debug/chat"
CHUNK_URL = "/api/v1/apps/{app_id}/debug/chunks/{chunk_id}/preview"


# ---- 假向量化 / 重排 ----


class FakeEmbedding:
    """固定向量的假向量化（P4-07 测试同款）。"""

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [1, 0, 0, 0]

    async def __call__(self, **kwargs):
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=list(self.vector)) for _ in kwargs["input"]],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0),
        )


class FakeRerank:
    """按输入顺序返回递减相关度的假重排。"""

    async def __call__(self, **kwargs):
        documents = kwargs["documents"]
        top_n = kwargs.get("top_n") or len(documents)
        return SimpleNamespace(
            results=[
                SimpleNamespace(index=index, relevance_score=1.0 - index * 0.1)
                for index in range(len(documents))
            ][:top_n]
        )


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


# ---- 公共助手 ----


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


def parse_events(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def make_pdf(pages: int) -> bytes:
    doc = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            doc.new_page(200, 200)
        buffer = io.BytesIO()
        doc.save(buffer)
        return buffer.getvalue()
    finally:
        doc.close()


async def make_app(session: AsyncSession, org, owner, config: KbQaAppConfig, **kwargs) -> App:
    resource = Resource(
        type="app",
        name=f"调试应用-{uuid.uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility="org",
    )
    session.add(resource)
    await session.flush()
    app = App(
        id=resource.id,
        app_type="kb_qa",
        draft_config=config.model_dump(mode="json"),
        draft_revision=kwargs.get("revision", 1),
        usage_scope="platform",
    )
    session.add(app)
    await session.commit()
    await session.refresh(app)
    return app


async def make_doc(
    session: AsyncSession, kb_id: int, owner_id: int, *, name: str, file_type: str, ext: str
) -> Document:
    doc = Document(
        kb_id=kb_id,
        folder_id=None,
        name=name,
        file_type=file_type,
        ext=ext,
        size=1,
        sha256=uuid.uuid4().hex * 2,
        status="done",
        current_generation=1,
        uploaded_by=owner_id,
        run_by=owner_id,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc


def make_candidate() -> AnswerCandidate:
    return AnswerCandidate(
        kb_id=101,
        kb_name="员工制度库",
        doc_id=3021,
        doc_name="员工考勤管理制度.docx",
        file_type=FileType.word,
        chunk_ids=[5512],
        generation=2,
        content="第十八条 新入职员工……",
        title_path=None,
        positions=[{"page": 1, "x": 0.1}],
        edited=False,
        score=0.83,
    )


def patch_retrieve(monkeypatch, candidates: list) -> None:
    async def fake_retrieve(run, emit):
        await pipeline.begin_step(run, emit, StepName.retrieve)
        await pipeline.end_step(
            run, emit, StepName.retrieve, input=run.query_text, output={"count": len(candidates)}
        )
        return list(candidates)

    monkeypatch.setattr(pipeline, "_retrieve", fake_retrieve)


async def setup_app(
    session: AsyncSession,
    monkeypatch,
    *,
    with_kb: bool = True,
    kb_id: int | None = None,
    **config_overrides,
) -> tuple:
    """建一个带草稿配置的应用（不需要发布版本）；默认关掉意图识别与改写。"""
    org = await make_org(session, f"调试组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    model = await make_chat_model(session, name=f"回答模型-{uuid.uuid4().hex[:6]}")
    if kb_id is None and with_kb:
        kb, _ = await make_kb(session, f"调试库-{uuid.uuid4().hex[:6]}")
        kb_id = kb.id
    kbs = [AppKbRef(kb_id=kb_id)] if kb_id is not None else []
    defaults: dict = {
        "intent": AppIntentConfig(enabled=False),
        "rewrite": AppRewriteConfig(enabled=False),
        "knowledge": AppKnowledgeConfig(kbs=kbs),
    }
    defaults.update(config_overrides)
    config = build_config(model.id, **defaults)
    app = await make_app(session, org, owner, config)
    return org, owner, model, app, config, kb_id


async def debug_chat(client: AsyncClient, app_id: int, **body) -> object:
    payload = {"draft_revision": 1, "question": "考勤怎么规定？"}
    payload.update(body)
    return await client.post(CHAT_URL.format(app_id=app_id), json=payload)


# ---- 调试 SSE、TRACE、不落库、用量 ----


async def test_debug_stream_events_trace_and_no_persistence(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, model, app, config, kb_id = await setup_app(session, monkeypatch)
    patch_retrieve(monkeypatch, [make_candidate()])
    fake_llm.stream_reply = "这是回答[1]"
    app_id = app.id
    login_as(client, owner)

    response = await debug_chat(client, app_id)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_events(response.text)
    types = [event["type"] for event in events]

    assert types[0] == "RUN_STARTED"
    started = events[0]
    assert started["conversation_id"] == 0 and started["turn_id"] == 0
    assert started["answer_id"] == 0
    assert "STEP_STARTED" in types and "STEP_FINISHED" in types
    assert "CITATIONS" in types and "TEXT_DELTA" in types
    assert "RUN_FINISHED" in types
    # 调试预览最后多一个 TRACE。
    assert types[-1] == "TRACE"
    assert "".join(e["delta"] for e in events if e["type"] == "TEXT_DELTA") == "这是回答[1]"
    citations = next(event for event in events if event["type"] == "CITATIONS")
    assert citations["citations"][0]["n"] == 1
    assert citations["citations"][0]["positions"] is None

    trace = events[-1]["trace"]
    assert trace["app_id"] == app_id and trace["answer_id"] == 0
    assert isinstance(trace["steps"], list)
    steps = {step["name"]: step for step in trace["steps"]}
    assert StepName.retrieve.value in steps and StepName.generate.value in steps
    generate = steps[StepName.generate.value]
    assert generate["model_id"] == model.id and generate["model_name"] == model.name
    assert generate["input_tokens"] == 11 and generate["output_tokens"] == 7
    assert generate["prompt_version"]
    assert generate["duration_ms"] >= 0
    assert generate["input"]  # 最终提示词全文

    await session.rollback()
    assert await session.scalar(sa.select(sa.func.count()).select_from(Conversation)) == 0
    assert await session.scalar(sa.select(sa.func.count()).select_from(ChatTurn)) == 0
    assert await session.scalar(sa.select(sa.func.count()).select_from(ChatAnswer)) == 0
    assert (
        await session.scalar(
            sa.select(sa.func.count()).select_from(AnswerTrace).where(AnswerTrace.app_id == app_id)
        )
        == 0
    )
    usage = (await session.execute(sa.select(ModelUsage))).scalars().all()
    assert usage and all(row.source == "app_debug" and row.source_id == app_id for row in usage)


async def test_debug_trace_records_recall_fusion_and_rerank(
    app,
    client: AsyncClient,
    session: AsyncSession,
    fake_llm,
    fake_embedding,
    fake_rerank,
) -> None:
    """用真实检索（假向量化 / 重排）验证 TRACE 里的召回与重排阶段。"""
    kb, kb_owner_id = await make_kb(session, f"追踪库-{uuid.uuid4().hex[:6]}", dim=4)
    await create_index_table(session, kb.id, 4)
    await session.commit()
    long_content = "长江大桥横跨长江，是重要的交通枢纽。" * 10
    doc = await make_doc(session, kb.id, kb_owner_id, name="桥梁.pdf", file_type="pdf", ext="pdf")
    chunk = Chunk(
        kb_id=kb.id,
        doc_id=doc.id,
        generation=1,
        seq=1,
        kind="normal",
        title_path=None,
        content=long_content,
        positions=[{"page": 1}],
        source="auto",
        edited=False,
    )
    session.add(chunk)
    await session.flush()
    await session.execute(
        sa.text(
            f"INSERT INTO {index_table_name(kb.id)} "
            "(entry_type, chunk_id, doc_id, generation, active, embedding, title, body, keywords) "
            "VALUES ('chunk', :cid, :did, 1, true, '[1,0,0,0]', NULL, :body, NULL)"
        ),
        {"cid": chunk.id, "did": doc.id, "body": long_content},
    )
    await session.commit()

    rerank_model = await make_rerank_model(session)
    org = await make_org(session, f"追踪组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    model = await make_chat_model(session)
    config = build_config(
        model.id,
        intent=AppIntentConfig(enabled=True),
        rewrite=AppRewriteConfig(enabled=True),
        knowledge=AppKnowledgeConfig(
            kbs=[AppKbRef(kb_id=kb.id, retrieval=RetrievalConfig(mode="vector", top_k=5))],
            doc=AppDocConfig(
                top_k=5, rerank=AppRerankConfig(enabled=True, model_id=rerank_model.id, keep=1)
            ),
        ),
    )
    app_row = await make_app(session, org, owner, config)
    fake_llm.stream_reply = "长江大桥是交通枢纽[1]"
    login_as(client, owner)

    response = await debug_chat(
        client,
        app_row.id,
        history=[
            {"role": "user", "content": "上一问"},
            {"role": "assistant", "content": "上一答"},
        ],
    )
    assert response.status_code == 200, response.text
    events = parse_events(response.text)
    trace = events[-1]["trace"]
    steps = {step["name"]: step for step in trace["steps"]}
    assert {StepName.intent.value, StepName.rewrite.value} <= set(steps)
    assert StepName.retrieve.value in steps and StepName.rerank.value in steps

    retrieve = steps[StepName.retrieve.value]
    assert retrieve["input"] == {"question": "考勤怎么规定？"} or retrieve["input"]
    hit = retrieve["output"]["hits"][0]
    assert hit["chunk_id"] == chunk.id
    assert hit["doc_name"] == "桥梁.pdf"
    assert isinstance(hit["score"], float)
    assert hit["content"] == long_content[:100]

    rerank = steps[StepName.rerank.value]
    assert rerank["model_id"] == rerank_model.id
    assert rerank["output"]["hits"]

    generate = steps[StepName.generate.value]
    prompt_text = json.dumps(generate["input"], ensure_ascii=False)
    assert long_content[:50] in prompt_text
    assert generate["input_tokens"] == 11 and generate["output_tokens"] == 7
    assert generate["prompt_version"]


# ---- 草稿版本与配置完整性 ----


async def test_debug_draft_conflict(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, model, app, config, kb_id = await setup_app(session, monkeypatch)
    patch_retrieve(monkeypatch, [make_candidate()])
    login_as(client, owner)

    response = await debug_chat(client, app.id, draft_revision=app.draft_revision + 1)
    assert response.status_code == 409
    assert response.json()["code"] == "DRAFT_CONFLICT"


async def test_debug_incomplete_config_lists_missing_items(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    # 没选模型。
    org, owner, model, app, config, kb_id = await setup_app(session, monkeypatch)
    config.model.chat_model_id = None
    app.draft_config = config.model_dump(mode="json")
    await session.commit()
    login_as(client, owner)
    response = await debug_chat(client, app.id)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    codes = [problem["code"] for problem in body["detail"]["problems"]]
    assert codes == ["MODEL_REQUIRED"]

    # 没选知识库。
    org2, owner2, model2, app2, config2, _ = await setup_app(session, monkeypatch, with_kb=False)
    login_as(client, owner2)
    response = await debug_chat(client, app2.id)
    assert response.status_code == 422
    body = response.json()
    codes = [problem["code"] for problem in body["detail"]["problems"]]
    assert codes == ["KB_REQUIRED"]


# ---- 历史：最多 20 轮、按 memory_turns 截取 ----


async def test_debug_history_truncated_by_memory_turns(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, model, app, config, kb_id = await setup_app(session, monkeypatch, memory_turns=2)
    patch_retrieve(monkeypatch, [make_candidate()])
    login_as(client, owner)

    history = [
        {"role": "user", "content": "第一问标记"},
        {"role": "assistant", "content": "第一答标记"},
        {"role": "user", "content": "第二问标记"},
        {"role": "assistant", "content": "第二答标记"},
        {"role": "user", "content": "第三问标记"},
        {"role": "assistant", "content": "第三答标记"},
    ]
    response = await debug_chat(client, app.id, history=history)
    assert response.status_code == 200, response.text
    events = parse_events(response.text)
    trace = events[-1]["trace"]
    steps = {step["name"]: step for step in trace["steps"]}
    prompt_text = json.dumps(steps[StepName.generate.value]["input"], ensure_ascii=False)
    # 只带最近 2 轮。
    assert "第一问标记" not in prompt_text and "第一答标记" not in prompt_text
    assert "第二问标记" in prompt_text and "第三问标记" in prompt_text


async def test_debug_history_over_20_turns_rejected(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, model, app, config, kb_id = await setup_app(session, monkeypatch)
    patch_retrieve(monkeypatch, [make_candidate()])
    login_as(client, owner)
    history = [
        ({"role": "user", "content": f"问题 {index}"})
        if index % 2 == 0
        else {"role": "assistant", "content": "答"}
        for index in range(42)
    ]
    response = await debug_chat(client, app.id, history=history)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


# ---- 调试分块预览 ----


async def seed_chunk_preview(
    session: AsyncSession, app, org, owner, *, pages: int = 3, page: int = 2
) -> tuple[int, int, int]:
    """建一个被应用引用的文档库 + 文档 + 分块，返回（知识库 id, 分块 id）。"""
    resource = Resource(
        type="kb",
        name=f"预览库-{uuid.uuid4().hex[:6]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility="org",
    )
    session.add(resource)
    await session.flush()
    embedding = Model(
        name=f"向量模型-{uuid.uuid4().hex[:6]}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=4,
        created_by=owner.id,
    )
    session.add(embedding)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind="doc",
        embedding_model_id=embedding.id,
        embedding_dim=4,
        index_table=f"kb_index.kb_{resource.id}",
    )
    session.add(kb)
    await session.flush()
    doc = await make_doc(session, kb.id, owner.id, name="制度.pdf", file_type="pdf", ext="pdf")
    chunk = Chunk(
        kb_id=kb.id,
        doc_id=doc.id,
        generation=1,
        seq=1,
        kind="normal",
        title_path=None,
        content="被引用的分块内容",
        positions=[{"page": page, "bbox": [0, 0, 1, 1]}],
        source="auto",
        edited=False,
    )
    session.add(chunk)
    await session.flush()
    config = dict(app.draft_config)
    config["knowledge"] = {**config["knowledge"], "kbs": [{"kb_id": kb.id}]}
    app.draft_config = config
    await session.commit()
    return kb.id, chunk.id, doc.id


async def test_debug_chunk_preview_whole_vs_partial_and_permissions(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"预览组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    other_org = await make_org(session, f"外部组织-{uuid.uuid4().hex[:6]}")
    collaborator = await make_user(session, Role.builder, other_org)
    end_user = await make_user(session, Role.end_user, org)
    model = await make_chat_model(session)
    config = build_config(
        model.id,
        intent=AppIntentConfig(enabled=False),
        rewrite=AppRewriteConfig(enabled=False),
        knowledge=AppKnowledgeConfig(kbs=[]),
    )
    app_row = await make_app(session, org, owner, config)
    kb_id, chunk_id, doc_id = await seed_chunk_preview(session, app_row, org, owner)
    await app.state.storage.put_bytes(kb_original_key(kb_id, doc_id, "pdf"), make_pdf(3))
    # 协作者是应用成员（有“编辑”），但对知识库没有查看权限。
    session.add(ResourceMember(resource_id=app_row.id, user_id=collaborator.id, added_by=owner.id))
    await session.commit()
    app_id = app_row.id

    # 搭建者（创建人）对知识库有查看权限：返回整份（3 页）。
    login_as(client, owner)
    whole = await client.get(CHUNK_URL.format(app_id=app_id, chunk_id=chunk_id))
    assert whole.status_code == 200, whole.text
    assert whole.headers["content-type"].startswith("application/pdf")
    with pdfium.PdfDocument(whole.content) as preview:
        assert len(preview) == 3

    # 协作者没有知识库查看权限：只返回被引用页（1 页）。
    login_as(client, collaborator)
    partial = await client.get(CHUNK_URL.format(app_id=app_id, chunk_id=chunk_id))
    assert partial.status_code == 200, partial.text
    with pdfium.PdfDocument(partial.content) as preview:
        assert len(preview) == 1

    # 终端用户对应用没有“编辑”权限：403。
    login_as(client, end_user)
    forbidden = await client.get(CHUNK_URL.format(app_id=app_id, chunk_id=chunk_id))
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "PERMISSION_DENIED"


async def test_debug_chunk_preview_requires_referenced_kb(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"未引用组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    model = await make_chat_model(session)
    config = build_config(
        model.id,
        intent=AppIntentConfig(enabled=False),
        rewrite=AppRewriteConfig(enabled=False),
        knowledge=AppKnowledgeConfig(kbs=[]),
    )
    app_row = await make_app(session, org, owner, config)
    _, chunk_id, _ = await seed_chunk_preview(session, app_row, org, owner)
    # 把草稿里引用的知识库清掉：分块所在知识库未被引用。
    config = dict(app_row.draft_config)
    config["knowledge"] = {**config["knowledge"], "kbs": []}
    app_row.draft_config = config
    await session.commit()
    login_as(client, owner)

    response = await client.get(CHUNK_URL.format(app_id=app_row.id, chunk_id=chunk_id))
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
