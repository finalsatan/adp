"""模型接口。连通测试与模型体验（SSE）见 P2-06、P2-07。

权限：增改删用 `model.manage`；列表、详情、已保存模型的测试、模型体验用 `console.use`。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget, record
from app.core.db import get_session
from app.core.redis import get_redis
from app.core.schemas import AuditResult, Page, Pagination
from app.iam.deps import CurrentCtx, require_perm
from app.models import playground, service, testing
from app.models.schemas import (
    ModelCreate,
    ModelOut,
    ModelRefOut,
    ModelStatus,
    ModelTestIn,
    ModelTestResult,
    ModelType,
    ModelUpdate,
    PlaygroundChatIn,
)

router = APIRouter(prefix="/api/v1", tags=["模型"])

CONSOLE_USE = [Depends(require_perm("console.use"))]
MODEL_MANAGE = [Depends(require_perm("model.manage"))]

SSE_RESPONSES = {
    200: {
        "description": "SSE 事件流",
        "content": {"text/event-stream": {"schema": {"$ref": "#/components/schemas/SseEvent"}}},
    }
}


def _target(model: ModelOut) -> AuditTarget:
    """审计对象：类型 model，desc 显示名称，模型不属于任何组织。"""
    return AuditTarget("model", model.id, model.name, None)


@router.get(
    "/models",
    response_model=Page[ModelOut],
    dependencies=CONSOLE_USE,
    summary="模型列表",
)
async def list_models(
    pagination: Annotated[Pagination, Depends()],
    session: Annotated[AsyncSession, Depends(get_session)],
    type: ModelType | None = None,
    status: ModelStatus | None = None,
    keyword: str | None = None,
) -> Page[ModelOut]:
    return await service.list_models(session, pagination, type, status, keyword)


@router.post(
    "/models",
    response_model=ModelOut,
    status_code=201,
    dependencies=[Depends(audited("model.create")), *MODEL_MANAGE],
    summary="添加模型",
)
async def create_model(
    body: ModelCreate,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelOut:
    model = await service.create_model(session, ctx, body)
    out = await service.model_out(session, model)
    await audit_success(request, _target(out))
    return out


@router.post(
    "/models:test",
    response_model=ModelTestResult,
    dependencies=MODEL_MANAGE,
    summary="测试未保存的模型连通",
)
async def test_model_form(
    body: ModelTestIn,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> ModelTestResult:
    return await testing.test_form(
        session, request.app.state.sessionmaker, redis, testing.call_meta(ctx), body
    )


@router.get(
    "/models/{id:int}",
    response_model=ModelOut,
    dependencies=CONSOLE_USE,
    summary="模型详情",
)
async def get_model(id: int, session: Annotated[AsyncSession, Depends(get_session)]) -> ModelOut:
    model = await service.get_model(session, id)
    return await service.model_out(session, model)


@router.patch(
    "/models/{id:int}",
    response_model=ModelOut,
    dependencies=[Depends(audited("model.update")), *MODEL_MANAGE],
    summary="编辑模型",
)
async def update_model(
    id: int,
    body: ModelUpdate,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelOut:
    model, events = await service.update_model(session, id, body)
    out = await service.model_out(session, model)
    target = _target(out)
    for action, detail in events:
        await record(ctx, action, target, AuditResult.success, detail)
    return out


@router.get(
    "/models/{id:int}/refs",
    response_model=list[ModelRefOut],
    dependencies=CONSOLE_USE,
    summary="模型引用清单",
)
async def list_model_refs(
    id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[ModelRefOut]:
    return await service.list_refs(session, id)


@router.post(
    "/models/{id:int}:disable",
    response_model=ModelOut,
    dependencies=[Depends(audited("model.disable")), *MODEL_MANAGE],
    summary="停用模型",
)
async def disable_model(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelOut:
    model = await service.set_status(session, id, enabled=False)
    out = await service.model_out(session, model)
    await audit_success(request, _target(out))
    return out


@router.post(
    "/models/{id:int}:enable",
    response_model=ModelOut,
    dependencies=[Depends(audited("model.enable")), *MODEL_MANAGE],
    summary="启用模型",
)
async def enable_model(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelOut:
    model = await service.set_status(session, id, enabled=True)
    out = await service.model_out(session, model)
    await audit_success(request, _target(out))
    return out


@router.delete(
    "/models/{id:int}",
    status_code=204,
    dependencies=[Depends(audited("model.delete")), *MODEL_MANAGE],
    summary="删除模型",
)
async def delete_model(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    model = await service.delete_model(session, id)
    await audit_success(request, AuditTarget("model", model.id, model.name, None))


@router.post(
    "/models/{id:int}:test",
    response_model=ModelTestResult,
    dependencies=CONSOLE_USE,
    summary="测试已保存模型的连通",
)
async def test_model(
    id: int,
    request: Request,
    ctx: CurrentCtx,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> ModelTestResult:
    model = await service.get_model(session, id)
    return await testing.test_saved_model(
        session, request.app.state.sessionmaker, redis, testing.call_meta(ctx), model
    )


@router.post(
    "/models/{id:int}/chat",
    response_class=StreamingResponse,
    dependencies=CONSOLE_USE,
    summary="模型体验对话（SSE）",
    responses=SSE_RESPONSES,
)
async def playground_chat(
    id: int,
    body: PlaygroundChatIn,
    request: Request,
    ctx: CurrentCtx,
    redis: Annotated[Redis, Depends(get_redis)],
) -> StreamingResponse:
    return await playground.chat(request, request.app.state.sessionmaker, redis, ctx, id, body)
