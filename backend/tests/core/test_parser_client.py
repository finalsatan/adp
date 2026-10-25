"""解析服务 HTTP 客户端（P3-02）：用 `httpx.MockTransport` 验证路径、方法与认证。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio

from app.core.config import Settings
from app.core.errors import ParserUnavailableError
from app.kb.parser_client import ParserClient


class _Recorder:
    def __init__(self, responses: dict[str, httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = responses or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.path}"
        return self._responses.get(key, httpx.Response(200, json={}))


@pytest_asyncio.fixture
async def make_client(settings: Settings) -> AsyncIterator[Callable[..., ParserClient]]:
    clients: list[ParserClient] = []

    def _make(handler) -> ParserClient:
        client = ParserClient(settings, transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.aclose()


def _assert_auth(request: httpx.Request, settings: Settings) -> None:
    assert request.headers["authorization"] == f"Bearer {settings.PARSER_TOKEN}"


async def test_submit_job(make_client, settings: Settings) -> None:
    recorder = _Recorder({"POST /v1/jobs": httpx.Response(202, json={"id": "job-1"})})
    client = make_client(recorder)
    result = await client.submit_job({"source_key": "kb/1/docs/2/original.pdf"})
    assert result == {"id": "job-1"}
    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/jobs"
    _assert_auth(request, settings)


async def test_get_job(make_client, settings: Settings) -> None:
    recorder = _Recorder({"GET /v1/jobs/job-1": httpx.Response(200, json={"id": "job-1"})})
    client = make_client(recorder)
    assert await client.get_job("job-1") == {"id": "job-1"}
    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/jobs/job-1"
    _assert_auth(request, settings)


async def test_cancel_job(make_client, settings: Settings) -> None:
    recorder = _Recorder({"DELETE /v1/jobs/job-1": httpx.Response(200, json={"id": "job-1"})})
    client = make_client(recorder)
    assert await client.cancel_job("job-1") == {"id": "job-1"}
    request = recorder.requests[0]
    assert request.method == "DELETE"
    assert request.url.path == "/v1/jobs/job-1"
    _assert_auth(request, settings)


async def test_list_engines(make_client, settings: Settings) -> None:
    recorder = _Recorder({"GET /v1/engines": httpx.Response(200, json=[{"name": "pypdfium"}])})
    client = make_client(recorder)
    assert await client.list_engines() == [{"name": "pypdfium"}]
    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/engines"
    _assert_auth(request, settings)


async def test_convert(make_client, settings: Settings) -> None:
    recorder = _Recorder({"POST /v1/convert": httpx.Response(200, json={"key": "converted.docx"})})
    client = make_client(recorder)
    result = await client.convert({"source_key": "a.doc", "target_format": "docx"})
    assert result == {"key": "converted.docx"}
    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/convert"
    _assert_auth(request, settings)


async def test_health(make_client, settings: Settings) -> None:
    recorder = _Recorder({"GET /healthz": httpx.Response(200, json={"status": "ok"})})
    client = make_client(recorder)
    assert await client.health() == {"status": "ok"}
    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/healthz"
    _assert_auth(request, settings)


async def test_non_2xx_maps_to_parser_unavailable(make_client, settings: Settings) -> None:
    recorder = _Recorder(
        {"GET /v1/jobs/job-1": httpx.Response(500, json={"code": "ENGINE_ERROR", "message": "x"})}
    )
    client = make_client(recorder)
    with pytest.raises(ParserUnavailableError) as excinfo:
        await client.get_job("job-1")
    assert excinfo.value.status == 502
    assert excinfo.value.code == "PARSER_UNAVAILABLE"
    assert excinfo.value.detail == {"status_code": 500, "code": "ENGINE_ERROR"}
    # 令牌不进错误信息
    assert settings.PARSER_TOKEN not in str(excinfo.value)
    assert settings.PARSER_TOKEN not in str(excinfo.value.detail)


async def test_timeout_maps_to_parser_unavailable(make_client, settings: Settings) -> None:
    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = make_client(_timeout)
    with pytest.raises(ParserUnavailableError) as excinfo:
        await client.list_engines()
    assert excinfo.value.status == 502
    assert settings.PARSER_TOKEN not in str(excinfo.value)
