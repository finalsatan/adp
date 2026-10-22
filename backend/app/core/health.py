"""健康检查：/healthz 进程存活，/readyz 依赖就绪。都不在 /api/v1 下。"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter()

CHECK_TIMEOUT_SECONDS = 2


async def _check_postgres(request: Request) -> str:
    async def _run() -> None:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(_run(), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:
        # 只记错误类型，不泄漏连接串
        return type(exc).__name__
    return "ok"


async def _check_redis(request: Request) -> str:
    try:
        await asyncio.wait_for(request.app.state.redis.ping(), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:
        return type(exc).__name__
    return "ok"


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    checks = {
        "postgres": await _check_postgres(request),
        "redis": await _check_redis(request),
    }
    if all(value == "ok" for value in checks.values()):
        return JSONResponse(status_code=200, content={"status": "ok", "checks": checks})
    return JSONResponse(status_code=503, content={"status": "error", "checks": checks})
