"""创建应用、注册路由。"""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.audit import service as audit_service
from app.audit.router import router as audit_router
from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.core.db import create_engine, create_sessionmaker
from app.core.errors import register_error_handlers
from app.core.health import router as health_router
from app.core.logging import setup_logging
from app.core.middleware import RequestIdMiddleware
from app.core.redis import create_redis
from app.core.security import load_common_passwords
from app.core.storage import Storage
from app.gateway import configure_litellm
from app.gateway.events import sse_openapi_schemas
from app.iam.orgs_router import router as orgs_router
from app.iam.router import router as me_router
from app.iam.users_router import router as users_router
from app.kb import register as register_kb_types
from app.kb.documents.router import router as kb_documents_router
from app.kb.folders_router import router as kb_folders_router
from app.kb.router import router as kb_router
from app.kb.schemes_router import router as kb_schemes_router
from app.models.router import router as models_router
from app.resources.router import router as resources_router
from app.settings.router import router as settings_router
from app.usage.router import router as usage_router

APP_TITLE = "智能体开发平台"
APP_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # 关闭 LiteLLM 遥测/回调、不跟随重定向（必须在加载 litellm 之后、调用之前）。
    configure_litellm()
    setup_logging(settings.LOG_LEVEL)
    # 弱密码表在启动时加载一次；缺文件或为空直接启动失败
    load_common_passwords()

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
    audit_service.configure(app.state.sessionmaker)
    app.state.redis = create_redis(settings.REDIS_URL)
    app.state.storage = Storage(settings)
    register_kb_types(app.state.storage)

    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    for router in (
        auth_router,
        me_router,
        orgs_router,
        users_router,
        resources_router,
        audit_router,
        settings_router,
        models_router,
        usage_router,
        kb_router,
        kb_folders_router,
        kb_schemes_router,
        kb_documents_router,
    ):
        app.include_router(router)

    _install_openapi(app)
    return app


def _install_openapi(app: FastAPI) -> None:
    """自定义 OpenAPI：把 SSE 事件结构加进 components（SSE 内容不会自动生成）。"""

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas.update(sse_openapi_schemas())
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


app = create_app()
