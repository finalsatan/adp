"""对话日志测试（P4-12）。

连真实 PostgreSQL、Redis；引用预览用 pypdfium2 生成的多页 PDF 验证“整份 vs 被引用
部分”，不依赖真实解析服务。
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

import pypdfium2 as pdfium
import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps import refs
from app.apps.config import config_hash, default_config
from app.apps.tables import App, AppVersion
from app.chat.schemas import CitationOut, CitationType
from app.chat.tables import AnswerFeedback, AnswerTrace, ChatAnswer, ChatTurn, Conversation
from app.core.schemas import Role
from app.core.storage import kb_original_key
from app.kb.schemas import FileType
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource, ResourceMember
from tests.factories import auth_cookies, make_org, make_user

LIST_URL = "/api/v1/apps/{app_id}/logs/conversations"
DETAIL_URL = "/api/v1/apps/{app_id}/logs/conversations/{conv_id}"
PREVIEW_URL = "/api/v1/apps/{app_id}/logs/answers/{answer_id}/citations/{n}/preview"


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


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


async def make_kb(
    session: AsyncSession, org, owner, name: str, *, visibility: str = "org"
) -> KnowledgeBase:
    resource = Resource(
        type="kb", name=name, org_id=org.id, owner_id=owner.id, visibility=visibility
    )
    session.add(resource)
    await session.flush()
    model = Model(
        name=f"向量模型-{name}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=4,
        created_by=owner.id,
    )
    session.add(model)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind="doc",
        embedding_model_id=model.id,
        embedding_dim=4,
        index_table=f"kb_index.kb_{resource.id}",
    )
    session.add(kb)
    await session.commit()
    return kb


async def make_doc(session: AsyncSession, kb: KnowledgeBase, owner, *, name: str) -> int:
    from app.kb.tables import Document

    doc = Document(
        kb_id=kb.id,
        folder_id=None,
        name=name,
        file_type="pdf",
        ext="pdf",
        size=1,
        sha256=uuid.uuid4().hex * 2,
        status="done",
        current_generation=1,
        uploaded_by=owner.id,
        run_by=owner.id,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc.id


async def make_app(
    session: AsyncSession,
    org,
    owner,
    *,
    kb_ids: list[int] | None = None,
    visibility: str = "org",
    usage_scope: str = "platform",
) -> App:
    config = default_config().model_dump(mode="json")
    config["knowledge"]["kbs"] = [{"kb_id": kb_id} for kb_id in (kb_ids or [])]
    resource = Resource(
        type="app",
        name=f"应用-{uuid.uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility=visibility,
    )
    session.add(resource)
    await session.flush()
    app = App(
        id=resource.id,
        app_type="kb_qa",
        draft_config=config,
        draft_revision=1,
        usage_scope=usage_scope,
    )
    session.add(app)
    version = AppVersion(
        app_id=resource.id,
        version_no=1,
        config=config,
        config_hash=config_hash(config),
        note="初版",
        published_by=owner.id,
    )
    session.add(version)
    await session.flush()
    app.published_version_id = version.id
    await session.flush()
    await refs.recompute_for_app(session, app)
    await session.commit()
    await session.refresh(app)
    return app


def doc_citation(doc_id: int, kb_id: int, *, n: int = 1, **overrides) -> CitationOut:
    fields: dict = {
        "n": n,
        "type": CitationType.doc,
        "kb_id": kb_id,
        "kb_name": "知识库",
        "score": 0.9,
        "doc_id": doc_id,
        "doc_name": f"文档-{doc_id}.pdf",
        "file_type": FileType.pdf,
        "chunk_ids": [1],
        "generation": 1,
        "content": "快照里的片段",
        "positions": [{"page": 1}],
        "edited": False,
    }
    fields.update(overrides)
    return CitationOut(**fields)


async def make_conversation(
    session: AsyncSession,
    app: App,
    user,
    *,
    title: str = "会话",
    created_at: datetime | None = None,
    last_active_at: datetime | None = None,
    deleted: bool = False,
) -> Conversation:
    conversation = Conversation(app_id=app.id, user_id=user.id, title=title)
    if created_at is not None:
        conversation.created_at = created_at
    if last_active_at is not None:
        conversation.last_active_at = last_active_at
    if deleted:
        conversation.deleted_at = datetime.now(UTC)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def add_turn(
    session: AsyncSession,
    conversation: Conversation,
    app: App,
    *,
    seq: int,
    question: str = "问题",
    answers: list[tuple[str, list[CitationOut]]] | None = None,
    created_at: datetime | None = None,
    trace: list[dict] | None = None,
) -> ChatTurn:
    """建一问与若干回答；`answers` 为 `(内容, 引用)`，最后一条是当前回答。"""
    turn = ChatTurn(conversation_id=conversation.id, seq=seq, question=question)
    session.add(turn)
    await session.flush()
    created: list[ChatAnswer] = []
    for index, (content, citation_list) in enumerate(answers or [("回答", [])], start=1):
        answer = ChatAnswer(
            turn_id=turn.id,
            seq=index,
            version_id=app.published_version_id,
            kind="generated",
            status="done",
            content=content,
            citations=[citation.model_dump(mode="json") for citation in citation_list],
            cited=[citation.n for citation in citation_list],
        )
        if created_at is not None:
            answer.created_at = created_at
        session.add(answer)
        await session.flush()
        created.append(answer)
    turn.current_answer_id = created[-1].id
    if trace is not None:
        session.add(AnswerTrace(answer_id=created[-1].id, app_id=app.id, trace=trace))
    await session.commit()
    await session.refresh(turn)
    return turn


async def add_feedback(
    session: AsyncSession, app: App, answer: ChatAnswer, user, rating: str
) -> AnswerFeedback:
    feedback = AnswerFeedback(
        answer_id=answer.id,
        app_id=app.id,
        user_id=user.id,
        rating=rating,
    )
    session.add(feedback)
    await session.commit()
    return feedback


def _count(model, *conditions):
    return sa.select(sa.func.count()).select_from(model).where(*conditions)


@pytest_asyncio.fixture(autouse=True)
async def _clean_objects(app):
    await app.state.storage.delete_prefix("kb/")
    yield
    await app.state.storage.delete_prefix("kb/")


# ---- 会话列表 ----


async def test_log_list_fields_filters_and_pagination(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"日志组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner)

    now = datetime.now(UTC)
    recent = await make_conversation(session, app_row, user, title="年假折算", last_active_at=now)
    recent_turn = await add_turn(session, recent, app_row, seq=1, question="年假几天")
    recent_answer = await session.get(ChatAnswer, recent_turn.current_answer_id)
    assert recent_answer is not None
    await add_feedback(session, app_row, recent_answer, user, "down")

    liked = await make_conversation(
        session, app_row, user, title="公积金", last_active_at=now - timedelta(minutes=1)
    )
    liked_turn = await add_turn(session, liked, app_row, seq=1)
    liked_answer = await session.get(ChatAnswer, liked_turn.current_answer_id)
    assert liked_answer is not None
    await add_feedback(session, app_row, liked_answer, user, "up")

    old = await make_conversation(
        session,
        app_row,
        user,
        title="离职证明",
        last_active_at=now - timedelta(days=10),
    )
    await add_turn(session, old, app_row, seq=1, created_at=now - timedelta(days=10))

    login_as(client, owner)

    response = await client.get(LIST_URL.format(app_id=app_row.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    titles = [item["title"] for item in body["items"]]
    assert titles == ["年假折算", "公积金"]
    first = body["items"][0]
    assert first["conversation_id"] == recent.id
    assert first["app_id"] == app_row.id
    assert first["user"] == {"id": user.id, "username": user.username, "name": user.name}
    assert first["org"] == {"id": org.id, "name": org.name}
    assert first["turn_count"] == 1
    assert first["version_no"] == 1
    assert first["has_feedback"] is True
    assert first["has_down_feedback"] is True
    assert first["user_deleted"] is False

    down = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"feedback": "down", "range": "all"}
    )
    assert [item["title"] for item in down.json()["items"]] == ["年假折算"]
    up = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"feedback": "up", "range": "all"}
    )
    assert [item["title"] for item in up.json()["items"]] == ["公积金"]

    # 时间筛选：默认 7 天不含 10 天前的会话，`all` 含。
    assert down.json()["total"] == 1
    all_time = await client.get(LIST_URL.format(app_id=app_row.id), params={"range": "all"})
    assert all_time.json()["total"] == 3

    # 关键词按会话标题或用户搜索，LIKE 通配符按字面匹配。
    keyword = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "keyword": "年假"}
    )
    assert [item["title"] for item in keyword.json()["items"]] == ["年假折算"]
    literal = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "keyword": "%"}
    )
    assert literal.json()["total"] == 0

    # 分页：每页 2 条。
    page1 = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "page": 1, "page_size": 2}
    )
    assert page1.json()["total"] == 3
    assert len(page1.json()["items"]) == 2
    page2 = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "page": 2, "page_size": 2}
    )
    assert len(page2.json()["items"]) == 1


# ---- 会话回放 ----


async def test_log_detail_returns_all_answers_and_trace(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"回放组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner)
    conversation = await make_conversation(session, app_row, user, title="年假折算")
    trace = [
        {
            "name": "generate",
            "started_at": "2025-10-21T10:31:00+00:00",
            "duration_ms": 2600,
            "input": {"messages": [{"role": "system", "content": "你是助手"}]},
            "output": "回答内容",
            "model_id": None,
            "model_name": "通义千问-Max",
            "input_tokens": 2688,
            "output_tokens": 121,
            "prompt_version": "v1",
        }
    ]
    await add_turn(
        session,
        conversation,
        app_row,
        seq=1,
        question="我今年 7 月入职，能休几天？",
        answers=[("旧回答", []), ("新回答", [])],
        trace=trace,
    )

    login_as(client, owner)
    response = await client.get(DETAIL_URL.format(app_id=app_row.id, conv_id=conversation.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation_id"] == conversation.id
    assert body["user"]["id"] == user.id
    assert body["user_deleted"] is False
    assert len(body["turns"]) == 1
    returned = body["turns"][0]
    assert returned["question"] == "我今年 7 月入职，能休几天？"
    assert [answer["content"] for answer in returned["answers"]] == ["旧回答", "新回答"]
    assert returned["current_answer_id"] == returned["answers"][-1]["id"]
    # 旧回答标“已重新生成”由前端按 current_answer_id 判断，后端返回全部回答。
    assert returned["answers"][0]["id"] != returned["current_answer_id"]
    assert returned["answers"][0]["trace"] is None
    current = returned["answers"][-1]
    assert current["trace"]["answer_id"] == current["id"]
    assert current["trace"]["app_id"] == app_row.id
    assert current["trace"]["steps"][0]["name"] == "generate"
    assert current["trace"]["steps"][0]["duration_ms"] == 2600
    assert current["trace"]["steps"][0]["model_name"] == "通义千问-Max"
    assert current["trace"]["steps"][0]["input_tokens"] == 2688


async def test_user_deleted_conversation_still_visible(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"删除会话组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner)
    conversation = await make_conversation(session, app_row, user, title="离职证明", deleted=True)
    await add_turn(session, conversation, app_row, seq=1)

    login_as(client, owner)
    listing = await client.get(LIST_URL.format(app_id=app_row.id))
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["user_deleted"] is True

    detail = await client.get(DETAIL_URL.format(app_id=app_row.id, conv_id=conversation.id))
    assert detail.status_code == 200, detail.text
    assert detail.json()["user_deleted"] is True


# ---- 权限与删除 ----


async def test_view_only_permission_forbidden(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"权限组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    viewer = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner, visibility="org")
    conversation = await make_conversation(session, app_row, user)
    await add_turn(session, conversation, app_row, seq=1)

    login_as(client, viewer)
    listing = await client.get(LIST_URL.format(app_id=app_row.id))
    assert listing.status_code == 403
    assert listing.json()["code"] == "PERMISSION_DENIED"
    detail = await client.get(DETAIL_URL.format(app_id=app_row.id, conv_id=conversation.id))
    assert detail.status_code == 403

    login_as(client, owner)
    assert (await client.get(LIST_URL.format(app_id=app_row.id))).status_code == 200


async def test_deleted_app_returns_404_but_keeps_data(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"删应用组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner)
    conversation = await make_conversation(session, app_row, user)
    turn = await add_turn(session, conversation, app_row, seq=1)
    answer = await session.get(ChatAnswer, turn.current_answer_id)
    assert answer is not None
    await add_feedback(session, app_row, answer, user, "down")

    resource = await session.get(Resource, app_row.id)
    assert resource is not None
    resource.deleted_at = datetime.now(UTC)
    await session.commit()

    login_as(client, owner)
    listing = await client.get(LIST_URL.format(app_id=app_row.id))
    assert listing.status_code == 404
    detail = await client.get(DETAIL_URL.format(app_id=app_row.id, conv_id=conversation.id))
    assert detail.status_code == 404

    # 会话、回答与反馈仍在库中（12.2、5.4.2）。
    assert await session.scalar(_count(Conversation, Conversation.app_id == app_row.id)) == 1
    assert await session.scalar(_count(ChatAnswer, ChatAnswer.turn_id == turn.id)) == 1
    assert await session.scalar(_count(AnswerFeedback, AnswerFeedback.answer_id == answer.id)) == 1


# ---- 日志里的引用预览 ----


async def test_log_preview_only_referenced_kb(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"预览组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    kb_ref = await make_kb(session, org, owner, "引用库")
    kb_other = await make_kb(session, org, owner, "未引用库")
    doc_ref = await make_doc(session, kb_ref, owner, name="引用.pdf")
    doc_other = await make_doc(session, kb_other, owner, name="未引用.pdf")
    await app.state.storage.put_bytes(kb_original_key(kb_ref.id, doc_ref, "pdf"), make_pdf(2))
    app_row = await make_app(session, org, owner, kb_ids=[kb_ref.id])
    conversation = await make_conversation(session, app_row, user)
    turn = await add_turn(
        session,
        conversation,
        app_row,
        seq=1,
        answers=[
            (
                "回答[1][2]",
                [
                    doc_citation(doc_ref, kb_ref.id, n=1),
                    doc_citation(doc_other, kb_other.id, n=2),
                ],
            )
        ],
    )
    answer_id = turn.current_answer_id

    login_as(client, owner)
    referenced = await client.get(PREVIEW_URL.format(app_id=app_row.id, answer_id=answer_id, n=1))
    assert referenced.status_code == 200, referenced.text
    assert referenced.headers["content-type"].startswith("application/pdf")
    # 搭建者（本库创建人）对知识库有查看权限，返回整份原文。
    with pdfium.PdfDocument(referenced.content) as preview:
        assert len(preview) == 2

    not_referenced = await client.get(
        PREVIEW_URL.format(app_id=app_row.id, answer_id=answer_id, n=2)
    )
    assert not_referenced.status_code == 404

    # 没有“编辑”权限的终端用户不能看日志里的引用预览。
    login_as(client, user)
    forbidden = await client.get(PREVIEW_URL.format(app_id=app_row.id, answer_id=answer_id, n=1))
    assert forbidden.status_code == 403


async def test_log_preview_whole_vs_partial_by_kb_permission(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"整份组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    collab = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    # 知识库“仅自己”：协作者看不到，只能拿到被引用部分。
    kb = await make_kb(session, org, owner, "私有库", visibility="private")
    doc = await make_doc(session, kb, owner, name="制度.pdf")
    await app.state.storage.put_bytes(kb_original_key(kb.id, doc, "pdf"), make_pdf(3))
    app_row = await make_app(session, org, owner, kb_ids=[kb.id])
    session.add(ResourceMember(resource_id=app_row.id, user_id=collab.id, added_by=owner.id))
    await session.commit()
    conversation = await make_conversation(session, app_row, user)
    turn = await add_turn(
        session,
        conversation,
        app_row,
        seq=1,
        answers=[("回答[1]", [doc_citation(doc, kb.id, n=1)])],
    )
    answer_id = turn.current_answer_id

    login_as(client, owner)
    whole = await client.get(PREVIEW_URL.format(app_id=app_row.id, answer_id=answer_id, n=1))
    assert whole.status_code == 200, whole.text
    with pdfium.PdfDocument(whole.content) as preview:
        assert len(preview) == 3

    login_as(client, collab)
    partial = await client.get(PREVIEW_URL.format(app_id=app_row.id, answer_id=answer_id, n=1))
    assert partial.status_code == 200, partial.text
    with pdfium.PdfDocument(partial.content) as preview:
        assert len(preview) == 1
