"""创建应用、注册路由。"""

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.db import create_engine, create_sessionmaker
from app.core.errors import register_error_handlers
from app.core.health import router as health_router
from app.core.logging import setup_logging
from app.core.middleware import RequestIdMiddleware
from app.core.redis import create_redis

APP_TITLE = "智能体开发平台"
APP_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.LOG_LEVEL)

    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs" if settings.APP_ENV != "prod" else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = create_engine(settings.DATABASE_URL)
    app.state.sessionmaker = create_sessionmaker(app.state.engine)
    app.state.redis = create_redis(settings.REDIS_URL)

    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)

    return app


app = create_app()
