"""流式回答与生成控制测试（P4-09）。

连真实 PostgreSQL、Redis；模型调用用 `tests/chat/conftest.py` 的可控假模型
（默认流式），检索用固定候选替换（检索本身由 P4-07 覆盖）。断开取消用 uvicorn
起真实服务验证。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import litellm
import sqlalchemy as sa
import uvicorn
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import (
    AppIntentConfig,
    AppRewriteConfig,
    config_hash,
    default_config,
)
from app.apps.tables import App, AppUsageTarget, AppVersion
from app.chat import pipeline, streaming
from app.chat.retrieval import AnswerCandidate
from app.chat.schemas import StepName
from app.chat.tables import ChatAnswer, ChatTurn, Conversation
from app.core.schemas import Role
from app.gateway.ratelimit import RateLimiter
from app.gateway.tables import ModelUsage
from app.kb.schemas import FileType
from app.resources.tables import Resource
from tests.chat.conftest import build_config, make_chat_model
from tests.factories import auth_cookies, make_org, make_user


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


def text_chunk(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, reasoning_content=None))],
        usage=None,
    )


async def make_app(
    session: AsyncSession, org, owner, config: dict, *, usage_scope: str = "platform"
) -> App:
    resource = Resource(
        type="app",
        name=f"应用-{uuid.uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility="org",
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
    await session.commit()
    await session.refresh(app)
    return app


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
    session: AsyncSession, monkeypatch, fake_llm, *, intent: bool = False, rewrite: bool = False
):
    """建一个已发布的应用：回答模型 + 固定候选；默认关闭意图识别与改写。"""
    org = await make_org(session, f"流式组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    model = await make_chat_model(session)
    config = build_config(
        model.id,
        intent=AppIntentConfig(enabled=intent),
        rewrite=AppRewriteConfig(enabled=rewrite),
    )
    app = await make_app(session, org, owner, config.model_dump(mode="json"))
    return org, owner, user, model, app, config


async def create_conversation(client: AsyncClient, app_id: int) -> dict:
    response = await client.post(f"/api/v1/chat/apps/{app_id}/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()


# ---- 正常生成：事件顺序、CITATIONS、落库、用量 ----


async def test_generated_stream_events_and_persistence(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app, config = await setup_app(session, monkeypatch, fake_llm)
    patch_retrieve(monkeypatch, [make_candidate()])
    fake_llm.stream_reply = "这是回答[1]"
    app_id = app.id
    version_id = app.published_version_id
    model_id = model.id
    login_as(client, user)
    conversation = await create_conversation(client, app_id)

    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/turns", json={"question": "考勤怎么规定？"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_events(response.text)
    types = [event["type"] for event in events]

    assert types[0] == "RUN_STARTED"
    started = events[0]
    assert started["conversation_id"] == conversation["id"]
    assert started["turn_id"] > 0
    assert started["answer_id"] > 0
    assert types[-1] == "RUN_FINISHED"
    assert "STEP_STARTED" in types and "STEP_FINISHED" in types
    assert "CITATIONS" in types and "TEXT_DELTA" in types

    citations = next(event for event in events if event["type"] == "CITATIONS")
    assert citations["citations"][0]["n"] == 1
    assert citations["citations"][0]["type"] == "doc"
    assert citations["citations"][0]["positions"] is None
    assert "".join(e["delta"] for e in events if e["type"] == "TEXT_DELTA") == "这是回答[1]"
    finished = events[-1]
    assert finished["kind"] == "generated"
    assert finished["status"] == "done"
    assert finished["input_tokens"] == 11
    assert finished["output_tokens"] == 7
    assert finished["first_token_ms"] >= 0

    await session.rollback()
    turn = await session.scalar(
        sa.select(ChatTurn).where(ChatTurn.conversation_id == conversation["id"])
    )
    answer = await session.get(ChatAnswer, turn.current_answer_id)
    assert answer is not None
    assert answer.status == "done"
    assert answer.kind == "generated"
    assert answer.content == "这是回答[1]"
    assert answer.cited == [1]
    assert answer.citations[0]["doc_name"] == "员工考勤管理制度.docx"
    assert answer.citations[0]["positions"] == [{"page": 1, "x": 0.1}]
    assert answer.version_id == version_id
    assert answer.input_tokens == 11 and answer.output_tokens == 7
    stored = await session.get(Conversation, conversation["id"])
    assert stored is not None and stored.title == "考勤怎么规定？"

    usage = (await session.execute(sa.select(ModelUsage))).scalars().all()
    assert usage and all(row.source == "app" and row.source_id == app_id for row in usage)
    assert all(row.model_id == model_id for row in usage)


async def test_fallback_sends_single_delta_without_model(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app, config = await setup_app(session, monkeypatch, fake_llm)
    patch_retrieve(monkeypatch, [])
    login_as(client, user)
    conversation = await create_conversation(client, app.id)

    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/turns", json={"question": "无关问题"}
    )
    events = parse_events(response.text)
    deltas = [event for event in events if event["type"] == "TEXT_DELTA"]
    assert len(deltas) == 1
    assert deltas[0]["delta"] == config.reply.fallback_text
    assert events[-1]["type"] == "RUN_FINISHED"
    assert events[-1]["kind"] == "fallback"
    assert "CITATIONS" not in [event["type"] for event in events]
    assert "chat" not in fake_llm.calls


async def test_chitchat_uses_chat_model_without_citations(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app, config = await setup_app(
        session, monkeypatch, fake_llm, intent=True, rewrite=True
    )
    fake_llm.intent_reply = '{"intent": "chitchat"}'
    fake_llm.chat_reply = "你好呀"
    login_as(client, user)
    conversation = await create_conversation(client, app.id)

    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/turns", json={"question": "在吗"}
    )
    events = parse_events(response.text)
    assert events[-1]["type"] == "RUN_FINISHED"
    assert events[-1]["kind"] == "chitchat"
    assert "CITATIONS" not in [event["type"] for event in events]
    deltas = "".join(e["delta"] for e in events if e["type"] == "TEXT_DELTA")
    assert deltas == "你好呀"
    assert "chat" in fake_llm.calls


# ---- 出错与降级 ----


async def test_disabled_model_run_error(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app, config = await setup_app(session, monkeypatch, fake_llm)
    patch_retrieve(monkeypatch, [make_candidate()])
    model.status = "disabled"
    await session.commit()
    login_as(client, user)
    conversation = await create_conversation(client, app.id)

    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/turns", json={"question": "考勤"}
    )
    events = parse_events(response.text)
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == "MODEL_UNAVAILABLE"

    await session.rollback()
    turn = await session.scalar(
        sa.select(ChatTurn).where(ChatTurn.conversation_id == conversation["id"])
    )
    answer = await session.get(ChatAnswer, turn.current_answer_id)
    assert answer is not None and answer.status == "failed"


async def test_model_rate_limited_run_error(
    client: AsyncClient, session: AsyncSession, redis, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app, config = await setup_app(session, monkeypatch, fake_llm)
    patch_retrieve(monkeypatch, [make_candidate()])
    model.rate_concurrency = 1
    await session.commit()
    login_as(client, user)
    conversation = await create_conversation(client, app.id)

    limiter = RateLimiter(redis)
    async with limiter.acquire(model):
        response = await client.post(
            f"/api/v1/conversations/{conversation['id']}/turns", json={"question": "考勤"}
        )
    events = parse_events(response.text)
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == "MODEL_RATE_LIMITED"

    await session.rollback()
    turn = await session.scalar(
        sa.select(ChatTurn).where(ChatTurn.conversation_id == conversation["id"])
    )
    answer = await session.get(ChatAnswer, turn.current_answer_id)
    assert answer is not None and answer.status == "failed"


async def test_model_error_keeps_partial_and_marks_failed(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app, config = await setup_app(session, monkeypatch, fake_llm)
    patch_retrieve(monkeypatch, [make_candidate()])
    fake_llm.stream_reply = "已经输出的部分"
    fake_llm.stream_error = litellm.InternalServerError(
        message="boom", model="gpt-4o", llm_provider="openai"
    )
    login_as(client, user)
    conversation = await create_conversation(client, app.id)

    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/turns", json={"question": "考勤"}
    )
    events = parse_events(response.text)
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == "UPSTREAM_ERROR"

    await session.rollback()
    turn = await session.scalar(
        sa.select(ChatTurn).where(ChatTurn.conversation_id == conversation["id"])
    )
    answer = await session.get(ChatAnswer, turn.current_answer_id)
    assert answer is not None
    assert answer.status == "failed"
    assert answer.content == "已经输出的部分"


# ---- 会话生成锁 ----


async def test_conversation_busy_returns_409(
    client: AsyncClient, session: AsyncSession, redis, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app, config = await setup_app(session, monkeypatch, fake_llm)
    patch_retrieve(monkeypatch, [make_candidate()])
    login_as(client, user)
    conversation = await create_conversation(client, app.id)

    lock = await streaming.acquire_conversation_lock(redis, conversation["id"])
    try:
        response = await client.post(
            f"/api/v1/conversations/{conversation['id']}/turns", json={"question": "考勤"}
        )
        assert response.status_code == 409
        assert response.json()["code"] == "CONVERSATION_BUSY"
    finally:
        await lock.release()


async def test_conversation_lock_renews_and_releases(redis) -> None:
    lock = await streaming.acquire_conversation_lock(
        redis, 987654, lease_seconds=0.3, renew_interval=0.1
    )
    try:
        # 超过租约时长仍在集合里：续约生效，长时间生成不会丢锁。
        await asyncio.sleep(0.7)
        assert await redis.zcard("conv_gen:987654") == 1
    finally:
        await lock.release()
    assert await redis.zcard("conv_gen:987654") == 0


# ---- 重新生成 ----


async def test_regenerate_new_answer_and_not_last_turn(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app, config = await setup_app(session, monkeypatch, fake_llm)
    patch_retrieve(monkeypatch, [make_candidate()])
    login_as(client, user)
    conversation = await create_conversation(client, app.id)

    for question in ("第一问", "第二问"):
        response = await client.post(
            f"/api/v1/conversations/{conversation['id']}/turns", json={"question": question}
        )
        assert response.status_code == 200, response.text

    await session.rollback()
    turns = (
        (
            await session.execute(
                sa.select(ChatTurn)
                .where(ChatTurn.conversation_id == conversation["id"])
                .order_by(ChatTurn.seq)
            )
        )
        .scalars()
        .all()
    )
    first, second = turns
    assert second.current_answer_id is not None
    first_id = first.id
    second_id = second.id
    old_current = second.current_answer_id

    regenerated = await client.post(f"/api/v1/turns/{second_id}:regenerate")
    assert regenerated.status_code == 200, regenerated.text
    await session.rollback()
    refreshed = await session.get(ChatTurn, second_id)
    assert refreshed is not None
    assert refreshed.current_answer_id != old_current
    new_answer = await session.get(ChatAnswer, refreshed.current_answer_id)
    assert new_answer is not None and new_answer.seq == 2

    # 旧回答仍能读到（一问多答）。
    turns_response = await client.get(f"/api/v1/conversations/{conversation['id']}/turns")
    data = turns_response.json()
    assert [answer["seq"] for answer in data[1]["answers"]] == [1, 2]
    assert data[1]["current_answer_id"] == refreshed.current_answer_id

    # 只能重新生成最后一问。
    not_last = await client.post(f"/api/v1/turns/{first_id}:regenerate")
    assert not_last.status_code == 409
    assert not_last.json()["code"] == "NOT_LAST_TURN"


# ---- 断开取消 ----


class SlowStream:
    """先给一个片段，然后一直等；被取消时记下来。"""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()
        self.first_sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.first_sent:
            self.first_sent = True
            return text_chunk("部分回答")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def aclose(self) -> None:
        self.cancelled.set()


async def _wait_until(condition, message: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(message)


async def test_client_disconnect_marks_stopped(
    app, client: AsyncClient, session: AsyncSession, redis, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app_row, config = await setup_app(session, monkeypatch, fake_llm)
    patch_retrieve(monkeypatch, [make_candidate()])
    login_as(client, user)
    conversation = await create_conversation(client, app_row.id)

    stream = SlowStream()

    async def fake_acompletion(**kwargs):
        return stream

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    config_uv = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config_uv)
    server_task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/api/v1/conversations/{conversation['id']}/turns"
        async with httpx.AsyncClient() as http:
            http.cookies.update(auth_cookies(user))
            async with http.stream("POST", url, json={"question": "考勤"}) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if "TEXT_DELTA" in line:
                        break
            # 退出流式上下文即断开连接。

        await _wait_until(stream.cancelled.is_set, "客户端断开后上游没有被取消")

        await session.rollback()
        turn = await session.scalar(
            sa.select(ChatTurn).where(ChatTurn.conversation_id == conversation["id"])
        )
        answer_id = turn.current_answer_id
        deadline = asyncio.get_running_loop().time() + 5.0
        answer = None
        while asyncio.get_running_loop().time() < deadline:
            await session.rollback()
            answer = await session.get(ChatAnswer, answer_id)
            if answer is not None and answer.status == "stopped":
                break
            await asyncio.sleep(0.05)
        assert answer is not None and answer.status == "stopped"
        assert answer.content == "部分回答"
        assert answer.cited == []

        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            await session.rollback()
            rows = (await session.execute(sa.select(ModelUsage))).scalars().all()
            if rows:
                break
            await asyncio.sleep(0.05)
        assert rows and rows[-1].error_code == "CANCELLED"
        assert await redis.zcard(f"conv_gen:{conversation['id']}") == 0
    finally:
        server.should_exit = True
        await server_task


# ---- 使用范围 ----


async def test_removed_from_scope_ask_returns_403(
    client: AsyncClient, session: AsyncSession, fake_llm, monkeypatch
) -> None:
    org, owner, user, model, app, config = await setup_app(session, monkeypatch, fake_llm)
    patch_retrieve(monkeypatch, [make_candidate()])
    login_as(client, user)
    conversation = await create_conversation(client, app.id)

    other = await make_user(session, Role.end_user, org)
    app.usage_scope = "users"
    session.add(AppUsageTarget(app_id=app.id, target_type="user", target_id=other.id))
    await session.commit()

    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/turns", json={"question": "考勤"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "APP_NOT_IN_SCOPE"


def test_default_config_has_fallback() -> None:
    assert default_config().reply.fallback_text
