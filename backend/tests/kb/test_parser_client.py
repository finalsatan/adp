"""解析服务客户端的轮询等待与 404 重新提交（P3-09，知识库技术方案 6.1、6.3）。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ParserUnavailableError
from app.kb.parser_client import ParserClient, ParserJobTimeout


def _client(settings: Settings, handler) -> ParserClient:
    return ParserClient(settings, transport=httpx.MockTransport(handler))


def _status(state: str, *, done: int = 0, total: int = 0, result: Any = None) -> dict[str, Any]:
    return {
        "id": "job-1",
        "status": state,
        "progress": {"done_pages": done, "total_pages": total},
        "result": result,
        "error": None,
    }


async def test_wait_job_polls_until_succeeded(settings: Settings) -> None:
    """轮询到终态，逐次回调 `on_status`。"""
    gets = {"n": 0}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job-1"})
        gets["n"] += 1
        if gets["n"] == 1:
            return httpx.Response(200, json=_status("running", done=1, total=2))
        return httpx.Response(200, json=_status("succeeded", done=2, total=2, result={"pages": 2}))

    client = _client(settings, handler)
    try:

        async def on_status(status: dict[str, Any]) -> None:
            seen.append(status["status"])

        status = await client.wait_job({}, on_status=on_status, interval=0.001, timeout=5)
    finally:
        await client.aclose()

    assert status["status"] == "succeeded"
    assert status["result"] == {"pages": 2}
    assert seen == ["running", "succeeded"]
    assert gets["n"] == 2


async def test_wait_job_resubmits_on_404(settings: Settings) -> None:
    """查询作业 404（解析服务重启）时重新提交，最终成功。"""
    submissions = {"n": 0}
    gets = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            submissions["n"] += 1
            return httpx.Response(202, json={"id": f"job-{submissions['n']}"})
        gets["n"] += 1
        if gets["n"] == 1:
            return httpx.Response(404, json={"code": "JOB_NOT_FOUND", "message": "作业不存在"})
        return httpx.Response(200, json=_status("succeeded", done=1, total=1, result={}))

    client = _client(settings, handler)
    try:
        status = await client.wait_job({}, interval=0.001, timeout=5)
    finally:
        await client.aclose()

    assert status["status"] == "succeeded"
    assert submissions["n"] == 2


async def test_wait_job_timeout(settings: Settings) -> None:
    """等待超过作业超时抛 `ParserJobTimeout`。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job-1"})
        return httpx.Response(200, json=_status("running", done=0, total=10))

    client = _client(settings, handler)
    try:
        with pytest.raises(ParserJobTimeout) as excinfo:
            await client.wait_job({}, interval=0.001, timeout=0.01)
    finally:
        await client.aclose()

    assert excinfo.value.job_id == "job-1"


async def test_wait_job_propagates_other_errors(settings: Settings) -> None:
    """非 404 的错误（如 500）原样抛出，由流水线按临时错误重试。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job-1"})
        return httpx.Response(500, json={"code": "ENGINE_ERROR", "message": "x"})

    client = _client(settings, handler)
    try:
        with pytest.raises(ParserUnavailableError):
            await client.wait_job({}, interval=0.001, timeout=1)
    finally:
        await client.aclose()
