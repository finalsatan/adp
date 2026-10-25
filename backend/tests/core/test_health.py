"""健康检查。"""

from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


async def test_healthz(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_ok(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"postgres": "ok", "redis": "ok", "storage": "ok"}


async def test_readyz_redis_down(settings: Settings) -> None:
    bad_settings = settings.model_copy(update={"REDIS_URL": "redis://127.0.0.1:1/0"})
    application = create_app(bad_settings)
    try:
        transport = ASGITransport(app=application, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "error"
        assert body["checks"]["postgres"] == "ok"
        assert body["checks"]["redis"] != "ok"
    finally:
        await application.state.redis.aclose()
        await application.state.engine.dispose()
