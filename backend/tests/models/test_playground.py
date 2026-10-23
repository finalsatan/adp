"""模型体验 SSE 接口：事件顺序、思考内容、错误、类型/状态检查、用量来源（P2-07）。"""

import json
from types import SimpleNamespace

import litellm
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.gateway.tables import ModelUsage
from app.models.tables import Model
from tests.factories import auth_cookies, make_user


def text_chunk(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, reasoning_content=None))],
        usage=None,
    )


def reasoning_chunk(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content=text))],
        usage=None,
    )


def usage_chunk(input_tokens: int, output_tokens: int):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens),
    )


class FakeStream:
    """按顺序产出片段，耗尽后可抛一个异常。"""

    def __init__(self, chunks, error: BaseException | None = None):
        self._chunks = list(chunks)
        self._error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


async def make_model(session: AsyncSession, **overrides) -> tuple[Model, object]:
    user = await make_user(session, role=Role.sys_admin)
    fields = {
        "name": "体验对话模型",
        "status": "enabled",
        "created_by": user.id,
        "model_type": "chat",
        "litellm_model": "openai/gpt-4o",
        "cap_thinking": False,
        "default_params": {},
        "first_token_timeout_s": 30,
        "total_timeout_s": 300,
        "max_retries": 2,
        "context_length": 128000,
        "max_output": 4096,
    }
    fields.update(overrides)
    model = Model(**fields)
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model, user


def parse_events(text: str) -> list[dict]:
    """从 SSE 文本里取出所有 `data:` 事件（忽略 `: ping` 注释）。"""
    events = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def chat_body(**params) -> dict:
    return {"messages": [{"role": "user", "content": "你好"}], "params": params}


async def test_chat_events_and_usage(
    client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """正常对话：RUN_STARTED → TEXT_DELTA → RUN_FINISHED，响应头正确，用量来源 playground。"""
    model, user = await make_model(session)
    client.cookies.update(auth_cookies(user))

    async def fake_acompletion(**kwargs):
        return FakeStream([text_chunk("你好"), text_chunk("，世界"), usage_chunk(7, 3)])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    response = await client.post(f"/api/v1/models/{model.id}/chat", json=chat_body())

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    events = parse_events(response.text)
    assert events[0]["type"] == "RUN_STARTED"
    assert len(events[0]["run_id"]) == 32

    deltas = [event for event in events if event["type"] == "TEXT_DELTA"]
    assert "".join(event["delta"] for event in deltas) == "你好，世界"

    finished = events[-1]
    assert finished["type"] == "RUN_FINISHED"
    assert finished["input_tokens"] == 7
    assert finished["output_tokens"] == 3
    assert finished["first_token_ms"] >= 0
    assert finished["total_ms"] >= finished["first_token_ms"]

    rows = list((await session.execute(sa.select(ModelUsage))).scalars().all())
    assert len(rows) == 1
    assert rows[0].source == "playground"
    assert rows[0].model_id == model.id
    assert rows[0].user_id == user.id
    assert rows[0].ok is True


async def test_reasoning_delta(client: AsyncClient, session: AsyncSession, monkeypatch) -> None:
    """思考内容以 REASONING_DELTA 输出，且在文本之前。"""
    model, user = await make_model(session, cap_thinking=True)
    client.cookies.update(auth_cookies(user))

    async def fake_acompletion(**kwargs):
        return FakeStream([reasoning_chunk("先想一下"), text_chunk("答案"), usage_chunk(1, 2)])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    response = await client.post(f"/api/v1/models/{model.id}/chat", json=chat_body())

    assert response.status_code == 200, response.text
    events = parse_events(response.text)
    kinds = [event["type"] for event in events]
    assert "REASONING_DELTA" in kinds
    assert kinds.index("REASONING_DELTA") < kinds.index("TEXT_DELTA")
    assert [e["delta"] for e in events if e["type"] == "REASONING_DELTA"] == ["先想一下"]
    assert events[-1]["type"] == "RUN_FINISHED"


async def test_upstream_error_ends_with_run_error(
    client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """已输出片段后上游报错：最后一条是 RUN_ERROR，不再有 RUN_FINISHED。"""
    model, user = await make_model(session, max_retries=0)
    client.cookies.update(auth_cookies(user))
    error = litellm.InternalServerError(message="boom", model="gpt-4o", llm_provider="openai")

    async def fake_acompletion(**kwargs):
        return FakeStream([text_chunk("部分")], error=error)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    response = await client.post(f"/api/v1/models/{model.id}/chat", json=chat_body())

    assert response.status_code == 200, response.text
    events = parse_events(response.text)
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == "UPSTREAM_ERROR"
    assert events[-1]["message"]
    assert not any(event["type"] == "RUN_FINISHED" for event in events)


async def test_embedding_model_returns_422(client: AsyncClient, session: AsyncSession) -> None:
    """向量化模型发起对话：流式开始前返回 422。"""
    model, user = await make_model(
        session,
        name="向量模型",
        model_type="embedding",
        litellm_model="dashscope/text-embedding-v3",
        embedding_dim=1024,
        embedding_max_input=8192,
        embedding_batch_size=10,
    )
    client.cookies.update(auth_cookies(user))

    response = await client.post(f"/api/v1/models/{model.id}/chat", json=chat_body())

    assert response.status_code == 422
    assert response.json()["code"] == "MODEL_TYPE_MISMATCH"


async def test_disabled_model_returns_409(client: AsyncClient, session: AsyncSession) -> None:
    """停用模型发起对话：流式开始前返回 409。"""
    model, user = await make_model(session, status="disabled")
    client.cookies.update(auth_cookies(user))

    response = await client.post(f"/api/v1/models/{model.id}/chat", json=chat_body())

    assert response.status_code == 409
    assert response.json()["code"] == "MODEL_UNAVAILABLE"


async def test_max_tokens_capped_to_max_output(
    client: AsyncClient, session: AsyncSession, monkeypatch
) -> None:
    """max_tokens 超过 max_output 时按 max_output 截断。"""
    model, user = await make_model(session, max_output=128)
    client.cookies.update(auth_cookies(user))
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return FakeStream([usage_chunk(1, 1)])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    response = await client.post(
        f"/api/v1/models/{model.id}/chat", json=chat_body(max_tokens=99999)
    )

    assert response.status_code == 200, response.text
    assert captured["max_tokens"] == 128
