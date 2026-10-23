"""模型调用层：流式对话、重试、超时、取消与用量记录（P2-05）。"""

import asyncio
from types import SimpleNamespace

import litellm
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ModelHostNotAllowedError,
    ModelRateLimitedError,
    ModelUnavailableError,
)
from app.core.schemas import Role
from app.gateway import service
from app.gateway.errors import ModelCallError
from app.gateway.service import CallMeta
from app.gateway.tables import ModelUsage
from app.models.tables import Model
from tests.factories import make_user

META = CallMeta(source="playground", source_id=None, user_id=None, org_id=None)
MESSAGES = [{"role": "user", "content": "你好，介绍一下自己"}]


def deps(app):
    """服务函数需要的会话工厂和 Redis。"""
    return app.state.sessionmaker, app.state.redis


async def make_model(session: AsyncSession, **overrides) -> Model:
    user = await make_user(session, role=Role.sys_admin)
    fields = {
        "name": "测试对话模型",
        "status": "enabled",
        "created_by": user.id,
        "model_type": "chat",
        "litellm_model": "openai/gpt-4o",
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


async def usage_rows(session: AsyncSession) -> list[ModelUsage]:
    return list((await session.execute(sa.select(ModelUsage))).scalars().all())


def text_chunk(text: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content=text, reasoning_content=None)),
        ],
        usage=None,
    )


def usage_chunk(input_tokens: int, output_tokens: int):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens),
    )


class FakeStream:
    """可控的假流：按顺序产出片段，可指定在耗尽后抛出的异常。"""

    def __init__(self, chunks, error: BaseException | None = None, delay: float = 0.0):
        self._chunks = list(chunks)
        self._error = error
        self._delay = delay
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._chunks:
            return self._chunks.pop(0)
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


async def test_chat_stream_success_records_usage(app, session: AsyncSession, monkeypatch) -> None:
    """成功的流式对话：产出全部文本，用量取自用量片段，耗时字段有值。"""
    model = await make_model(session)

    # 用 LiteLLM 的 mock_response 模拟上游，不访问网络。
    real_acompletion = litellm.acompletion

    async def fake_acompletion(**kwargs):
        return await real_acompletion(**kwargs, mock_response="hello world")

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    chunks = [c async for c in service.chat_stream(*deps(app), META, model.id, MESSAGES, {})]

    assert "".join(c.text for c in chunks if c.text) == "hello world"
    final = [c for c in chunks if c.usage][-1]
    assert final.usage is not None and final.usage.input_tokens > 0
    assert final.usage.output_tokens > 0

    rows = await usage_rows(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.ok is True
    assert row.error_code is None
    assert row.input_tokens == final.usage.input_tokens
    assert row.output_tokens == final.usage.output_tokens
    assert row.first_token_ms is not None
    assert row.total_ms is not None
    assert row.model_name == model.name


async def test_retry_before_first_chunk_then_success(
    app, session: AsyncSession, monkeypatch
) -> None:
    """首片段之前两次超时、第三次成功（max_retries=2）：成功且只记一条用量。"""
    model = await make_model(session, max_retries=2)
    calls = {"n": 0}

    async def fake_acompletion(**kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise litellm.Timeout(message="timeout", model="gpt-4o", llm_provider="openai")
        return FakeStream([text_chunk("ok"), usage_chunk(3, 5)])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    chunks = [c async for c in service.chat_stream(*deps(app), META, model.id, MESSAGES, {})]

    assert calls["n"] == 3
    assert "".join(c.text for c in chunks if c.text) == "ok"
    rows = await usage_rows(session)
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].input_tokens == 3
    assert rows[0].output_tokens == 5


async def test_error_after_chunk_not_retried(app, session: AsyncSession, monkeypatch) -> None:
    """已输出片段后上游报错：不重试，抛 ModelCallError，用量 ok=false。"""
    model = await make_model(session, max_retries=3)
    calls = {"n": 0}
    error = litellm.InternalServerError(message="boom", model="gpt-4o", llm_provider="openai")

    async def fake_acompletion(**kwargs):
        calls["n"] += 1
        return FakeStream([text_chunk("部分")], error=error)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    with pytest.raises(ModelCallError) as exc:
        async for _ in service.chat_stream(*deps(app), META, model.id, MESSAGES, {}):
            pass
    assert exc.value.error_code == "UPSTREAM_ERROR"
    assert calls["n"] == 1
    rows = await usage_rows(session)
    assert len(rows) == 1
    assert rows[0].ok is False
    assert rows[0].error_code == "UPSTREAM_ERROR"


async def test_total_timeout_gives_up_retries(app, session: AsyncSession, monkeypatch) -> None:
    """总超时不足以再等一次首字：提前放弃重试。"""
    model = await make_model(session, first_token_timeout_s=1, total_timeout_s=2, max_retries=5)
    calls = {"n": 0}

    async def fake_acompletion(**kwargs):
        calls["n"] += 1
        await asyncio.sleep(3600)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    with pytest.raises(ModelCallError) as exc:
        async for _ in service.chat_stream(*deps(app), META, model.id, MESSAGES, {}):
            pass
    assert exc.value.error_code in ("FIRST_TOKEN_TIMEOUT", "TOTAL_TIMEOUT")
    assert calls["n"] < 6
    rows = await usage_rows(session)
    assert len(rows) == 1
    assert rows[0].ok is False
    assert rows[0].error_code == exc.value.error_code


async def test_upstream_auth_not_retried(app, session: AsyncSession, monkeypatch) -> None:
    """密钥无效（模拟 401）：不重试，错误代码 UPSTREAM_AUTH。"""
    model = await make_model(session, max_retries=3)
    calls = {"n": 0}

    async def fake_acompletion(**kwargs):
        calls["n"] += 1
        raise litellm.AuthenticationError(
            message="Incorrect API key", model="gpt-4o", llm_provider="openai"
        )

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    with pytest.raises(ModelCallError) as exc:
        async for _ in service.chat_stream(*deps(app), META, model.id, MESSAGES, {}):
            pass
    assert exc.value.error_code == "UPSTREAM_AUTH"
    assert calls["n"] == 1
    rows = await usage_rows(session)
    assert rows[0].ok is False
    assert rows[0].error_code == "UPSTREAM_AUTH"


async def test_disabled_and_deleted_model_unavailable(app, session: AsyncSession) -> None:
    disabled = await make_model(session, name="停用模型", status="disabled")
    with pytest.raises(ModelUnavailableError):
        async for _ in service.chat_stream(*deps(app), META, disabled.id, MESSAGES, {}):
            pass

    deleted = await make_model(session, name="已删除模型")
    deleted.deleted_at = sa.func.now()
    await session.commit()
    with pytest.raises(ModelUnavailableError):
        async for _ in service.chat_stream(*deps(app), META, deleted.id, MESSAGES, {}):
            pass


async def test_type_mismatch(app, session: AsyncSession) -> None:
    model = await make_model(
        session, model_type="embedding", litellm_model="openai/text-embedding-3-small"
    )
    from app.core.errors import ModelTypeMismatchError

    with pytest.raises(ModelTypeMismatchError):
        async for _ in service.chat_stream(*deps(app), META, model.id, MESSAGES, {}):
            pass


async def test_internal_host_rejected(app, session: AsyncSession) -> None:
    """内网地址的模型（未加白名单）被拒。"""
    model = await make_model(session, name="内网模型", api_base="http://127.0.0.1:8000")
    with pytest.raises(ModelHostNotAllowedError):
        async for _ in service.chat_stream(*deps(app), META, model.id, MESSAGES, {}):
            pass


async def test_rate_limited(app, session: AsyncSession, redis, monkeypatch) -> None:
    """并发 1 的模型，第二个在线调用返回 MODEL_RATE_LIMITED。"""
    model = await make_model(session, rate_concurrency=1)
    release = asyncio.Event()
    started = asyncio.Event()

    class BlockingStream(FakeStream):
        async def __anext__(self):
            if self._chunks:
                return self._chunks.pop(0)
            started.set()
            await release.wait()
            raise StopAsyncIteration

    async def fake_acompletion(**kwargs):
        return BlockingStream([text_chunk("hi")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    async def first_call():
        return [c async for c in service.chat_stream(*deps(app), META, model.id, MESSAGES, {})]

    task = asyncio.create_task(first_call())
    await started.wait()

    with pytest.raises(ModelRateLimitedError):
        async for _ in service.chat_stream(*deps(app), META, model.id, MESSAGES, {}):
            pass

    # 限流的请求没有调用上游，不写用量；第一个调用此时还没结束。
    assert await usage_rows(session) == []

    release.set()
    await task
    # 只有真正调用上游的第一个请求写了一条用量。
    assert len(await usage_rows(session)) == 1


async def test_cancelled_stream_releases_lease_and_records(
    app, session: AsyncSession, redis, monkeypatch
) -> None:
    """消费流式输出的任务被取消：上游被关闭、租约释放、用量记 CANCELLED 且估算 token。"""
    model = await make_model(session, rate_concurrency=1)
    got_first = asyncio.Event()

    class SlowStream:
        def __init__(self):
            self.closed = False
            self.count = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.count += 1
            if self.count == 1:
                return text_chunk("你好，我是助手")
            await asyncio.sleep(3600)

        async def aclose(self):
            self.closed = True

    stream = SlowStream()

    async def fake_acompletion(**kwargs):
        return stream

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    async def consume():
        async for _ in service.chat_stream(*deps(app), META, model.id, MESSAGES, {}):
            got_first.set()

    task = asyncio.create_task(consume())
    await got_first.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert stream.closed is True
    assert await redis.zcard(f"rl:conc:{model.id}:total") == 0
    rows = await usage_rows(session)
    assert len(rows) == 1
    assert rows[0].ok is False
    assert rows[0].error_code == "CANCELLED"
    assert rows[0].output_tokens > 0


@pytest.mark.parametrize(
    ("litellm_model", "key", "expected"),
    [
        ("openai/gpt-5", "reasoning_effort", "medium"),
        ("dashscope/qwen-max", "extra_body", {"enable_thinking": True}),
        ("deepseek/deepseek-chat", "thinking", {"type": "enabled"}),
    ],
)
async def test_thinking_param_mapping(
    app, session: AsyncSession, monkeypatch, litellm_model, key, expected
) -> None:
    """thinking 开关按供应商前缀映射成 LiteLLM 参数（dashscope / deepseek / openai）。"""
    model = await make_model(
        session, name=f"思考模型-{key}", litellm_model=litellm_model, cap_thinking=True
    )
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return FakeStream([usage_chunk(1, 1)])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    _ = [
        c
        async for c in service.chat_stream(*deps(app), META, model.id, MESSAGES, {"thinking": True})
    ]
    assert captured.get(key) == expected


async def test_thinking_ignored_without_capability(app, session: AsyncSession, monkeypatch) -> None:
    """cap_thinking 为假时忽略 thinking 参数。"""
    model = await make_model(session, cap_thinking=False)
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return FakeStream([usage_chunk(1, 1)])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    _ = [
        c
        async for c in service.chat_stream(*deps(app), META, model.id, MESSAGES, {"thinking": True})
    ]
    assert "reasoning_effort" not in captured
    assert "thinking" not in captured


async def test_request_params_override_default_params(
    app, session: AsyncSession, monkeypatch
) -> None:
    """default_params 打底，请求参数覆盖。"""
    model = await make_model(session, default_params={"temperature": 0.2, "top_p": 0.9})
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return FakeStream([usage_chunk(1, 1)])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    _ = [
        c
        async for c in service.chat_stream(
            *deps(app), META, model.id, MESSAGES, {"temperature": 0.7, "top_p": 0.9}
        )
    ]
    assert captured["temperature"] == 0.7
    assert captured["top_p"] == 0.9
