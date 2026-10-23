"""SSE 工具：心跳、事件序列化与客户端断开取消（P2-07）。

断开测试用 uvicorn 在随机端口起真实服务，验证客户端断开后上游调用确实被取消、
并发租约被释放、用量记录为 `CANCELLED`。
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import litellm
import sqlalchemy as sa
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.gateway import sse
from app.gateway.events import RunStartedEvent, TextDeltaEvent
from app.gateway.tables import ModelUsage
from app.models.tables import Model
from tests.factories import auth_cookies, make_user


def text_chunk(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, reasoning_content=None))],
        usage=None,
    )


def test_format_event_keeps_chinese() -> None:
    """事件是 `data: <json>\\n\\n`，中文不转义。"""
    frame = sse.format_event(TextDeltaEvent(delta="你好"))
    assert frame == 'data: {"type": "TEXT_DELTA", "delta": "你好"}\n\n'.encode()
    payload = json.loads(frame.decode().removeprefix("data: ").strip())
    assert payload == {"type": "TEXT_DELTA", "delta": "你好"}


async def test_heartbeat_before_first_event(monkeypatch) -> None:
    """心跳间隔 0.2 秒、1 秒后才出首个事件：首个事件前已有若干 `: ping`。"""
    monkeypatch.setattr(sse, "HEARTBEAT_INTERVAL_S", 0.2)

    async def events():
        await asyncio.sleep(1.0)
        yield RunStartedEvent(run_id="abc")
        yield TextDeltaEvent(delta="你好")

    response = sse.sse_response(events(), SimpleNamespace())
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
        if b"RUN_STARTED" in chunk:
            break

    assert chunks.count(sse.HEARTBEAT) >= 3
    first_event = next(i for i, chunk in enumerate(chunks) if b"RUN_STARTED" in chunk)
    assert all(chunk == sse.HEARTBEAT for chunk in chunks[:first_event])


class SlowStream:
    """第一个片段后一直等；被取消时记下来。"""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()
        self.first_sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.first_sent:
            self.first_sent = True
            return text_chunk("你好")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def aclose(self):
        self.cancelled.set()


async def _wait_until(condition, message: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(message)


async def test_client_disconnect_cancels_upstream(
    app, session: AsyncSession, redis, monkeypatch
) -> None:
    """客户端断开：上游被取消、并发租约释放、用量记 CANCELLED。"""
    user = await make_user(session, role=Role.sys_admin)
    model = Model(
        name="断线测试模型",
        status="enabled",
        created_by=user.id,
        model_type="chat",
        litellm_model="openai/gpt-4o",
        cap_thinking=False,
        default_params={},
        first_token_timeout_s=30,
        total_timeout_s=300,
        max_retries=0,
        rate_concurrency=1,
        context_length=128000,
        max_output=4096,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    model_id = model.id

    stream = SlowStream()

    async def fake_acompletion(**kwargs):
        return stream

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/api/v1/models/{model_id}/chat"

        async with httpx.AsyncClient() as http:
            async with http.stream(
                "POST",
                url,
                json={"messages": [{"role": "user", "content": "你好"}], "params": {}},
                cookies=auth_cookies(user),
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if "TEXT_DELTA" in line:
                        break
            # 退出流式上下文即关闭连接。

        await _wait_until(stream.cancelled.is_set, "客户端断开后上游没有被取消")

        # 用量记录在独立会话写入，轮询等待它落库。
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            await session.rollback()
            count = await session.scalar(sa.select(sa.func.count()).select_from(ModelUsage))
            if count:
                break
            await asyncio.sleep(0.05)

        rows = list((await session.execute(sa.select(ModelUsage))).scalars().all())
        assert len(rows) == 1, rows
        assert rows[0].error_code == "CANCELLED"
        assert rows[0].ok is False
        assert rows[0].source == "playground"
        assert await redis.zcard(f"rl:conc:{model_id}:total") == 0
    finally:
        server.should_exit = True
        await server_task
