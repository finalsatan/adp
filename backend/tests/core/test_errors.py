"""统一错误返回。往新建的应用上挂临时路由。"""

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.main import create_app


class _Body(BaseModel):
    n: int


@pytest_asyncio.fixture
async def error_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    application = create_app(settings)

    @application.get("/_test/app-error")
    async def _app_error() -> None:
        raise NotFoundError("没有这个东西", {"id": 1})

    @application.post("/_test/validation")
    async def _validation(body: _Body) -> dict[str, int]:
        return {"n": body.n}

    @application.get("/_test/unhandled")
    async def _unhandled() -> None:
        raise RuntimeError("boom")

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await application.state.redis.aclose()
    await application.state.engine.dispose()


async def test_app_error(error_client: AsyncClient) -> None:
    response = await error_client.get("/_test/app-error")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "没有这个东西"
    assert body["detail"] == {"id": 1}
    assert body["request_id"] == response.headers["x-request-id"]


async def test_validation_error(error_client: AsyncClient) -> None:
    response = await error_client.post("/_test/validation", json={"n": "not-an-int"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"]
    assert isinstance(body["detail"]["errors"], list)
    assert body["detail"]["errors"]
    assert body["request_id"] == response.headers["x-request-id"]


async def test_unknown_route(error_client: AsyncClient) -> None:
    response = await error_client.get("/_test/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["request_id"] == response.headers["x-request-id"]


async def test_unhandled_exception(error_client: AsyncClient) -> None:
    response = await error_client.get("/_test/unhandled")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["message"]
    assert "boom" not in response.text
    assert "Traceback" not in response.text
    assert body["request_id"] == response.headers["x-request-id"]
