"""`/readyz` 的对象存储检查（P3-02）。"""

from httpx import AsyncClient


async def test_readyz_storage_ok(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["storage"] == "ok"


async def test_readyz_storage_failure(app, client: AsyncClient, monkeypatch) -> None:
    """不真停 RustFS，monkeypatch check 抛异常。"""

    async def _boom() -> None:
        raise RuntimeError("rustfs down")

    monkeypatch.setattr(app.state.storage, "check", _boom)
    response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["storage"] == "RuntimeError"
    # 其它检查照常执行
    assert body["checks"]["postgres"] == "ok"
