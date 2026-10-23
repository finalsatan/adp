"""SSE 工具：事件序列化、心跳与客户端断开取消（技术方案 9.6，事件结构见 P2-01）。

每条事件一行 `data: <json>`；心跳是注释行 `: ping`。心跳和事件流合并在同一个
生成器里：等待事件的同时按 `HEARTBEAT_INTERVAL_S` 发心跳，事件一到立刻输出，
不会因为等心跳而推迟事件。

客户端断开由 Starlette 的 `StreamingResponse` 取消本生成器（所用版本走 ASGI 2.3
的任务组路径），生成器的 `finally` 会取消正在等待的 `__anext__` 任务，把取消
继续传给 gateway（gateway 随之取消上游、释放租约、记 `CANCELLED` 用量）。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.gateway.events import SseEvent

# 技术方案 9.6：每 15 秒没有其他输出时发一条注释。
HEARTBEAT_INTERVAL_S = 15.0
HEARTBEAT = b": ping\n\n"


def format_event(event: SseEvent) -> bytes:
    """把一条事件序列化成 SSE 的一帧；中文不转义。"""
    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"data: {payload}\n\n".encode()


def sse_response(
    events: AsyncIterator[SseEvent],
    request: Request,
    *,
    heartbeat_interval: float | None = None,
) -> StreamingResponse:
    """把事件流转成 SSE 响应，并加上关闭代理缓冲的响应头。"""
    interval = HEARTBEAT_INTERVAL_S if heartbeat_interval is None else heartbeat_interval

    async def stream() -> AsyncIterator[bytes]:
        # 单独的任务负责取下一个事件，这样等心跳时可以用 asyncio.wait 定时，
        # 超时不会取消 __anext__（取消会关掉上游的异步生成器）。
        anext_task = asyncio.ensure_future(events.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({anext_task}, timeout=interval)
                if anext_task not in done:
                    yield HEARTBEAT
                    continue
                try:
                    event = anext_task.result()
                except StopAsyncIteration:
                    break
                yield format_event(event)
                anext_task = asyncio.ensure_future(events.__anext__())
        finally:
            # 客户端断开（本生成器被取消）时，把取消传给取事件的任务。
            # 用 shield 等待：本任务被再次取消时不要再取消 anext_task，否则会把
            # gateway 正在写的 CANCELLED 用量记录一起取消掉。
            anext_task.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await asyncio.shield(anext_task)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
