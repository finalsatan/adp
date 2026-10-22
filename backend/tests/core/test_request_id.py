"""请求 ID 中间件。"""

from httpx import AsyncClient


async def test_valid_request_id_echoed(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-Id": "abc-123-XYZ"})
    assert response.headers["x-request-id"] == "abc-123-XYZ"


async def test_missing_request_id_generated(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    assert all(c in "0123456789abcdef" for c in request_id)


async def test_invalid_request_id_replaced(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-Id": "bad id!"})
    request_id = response.headers["x-request-id"]
    assert request_id != "bad id!"
    assert len(request_id) == 32


async def test_too_long_request_id_replaced(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-Id": "a" * 65})
    assert response.headers["x-request-id"] != "a" * 65
    assert len(response.headers["x-request-id"]) == 32
