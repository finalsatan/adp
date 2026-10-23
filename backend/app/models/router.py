"""模型接口。实现留空，P2-03 起逐张填充。

权限：增改删用 `model.manage`；列表、详情、已保存模型的测试、模型体验用 `console.use`。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.schemas import Page, Pagination
from app.iam.deps import require_perm
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


@router.get(
    "/models",
    response_model=Page[ModelOut],
    dependencies=CONSOLE_USE,
    summary="模型列表",
)
async def list_models(
    pagination: Annotated[Pagination, Depends()],
    type: ModelType | None = None,
    status: ModelStatus | None = None,
    keyword: str | None = None,
) -> Page[ModelOut]:
    raise NotImplementedError


@router.post(
    "/models",
    response_model=ModelOut,
    status_code=201,
    dependencies=MODEL_MANAGE,
    summary="添加模型",
)
async def create_model(body: ModelCreate) -> ModelOut:
    raise NotImplementedError


@router.post(
    "/models:test",
    response_model=ModelTestResult,
    dependencies=MODEL_MANAGE,
    summary="测试未保存的模型连通",
)
async def test_model_form(body: ModelTestIn) -> ModelTestResult:
    raise NotImplementedError


@router.get(
    "/models/{id:int}",
    response_model=ModelOut,
    dependencies=CONSOLE_USE,
    summary="模型详情",
)
async def get_model(id: int) -> ModelOut:
    raise NotImplementedError


@router.patch(
    "/models/{id:int}",
    response_model=ModelOut,
    dependencies=MODEL_MANAGE,
    summary="编辑模型",
)
async def update_model(id: int, body: ModelUpdate) -> ModelOut:
    raise NotImplementedError


@router.get(
    "/models/{id:int}/refs",
    response_model=list[ModelRefOut],
    dependencies=CONSOLE_USE,
    summary="模型引用清单",
)
async def list_model_refs(id: int) -> list[ModelRefOut]:
    raise NotImplementedError


@router.post(
    "/models/{id:int}:disable",
    response_model=ModelOut,
    dependencies=MODEL_MANAGE,
    summary="停用模型",
)
async def disable_model(id: int) -> ModelOut:
    raise NotImplementedError


@router.post(
    "/models/{id:int}:enable",
    response_model=ModelOut,
    dependencies=MODEL_MANAGE,
    summary="启用模型",
)
async def enable_model(id: int) -> ModelOut:
    raise NotImplementedError


@router.delete(
    "/models/{id:int}",
    status_code=204,
    dependencies=MODEL_MANAGE,
    summary="删除模型",
)
async def delete_model(id: int) -> None:
    raise NotImplementedError


@router.post(
    "/models/{id:int}:test",
    response_model=ModelTestResult,
    dependencies=CONSOLE_USE,
    summary="测试已保存模型的连通",
)
async def test_model(id: int) -> ModelTestResult:
    raise NotImplementedError


@router.post(
    "/models/{id:int}/chat",
    response_class=StreamingResponse,
    dependencies=CONSOLE_USE,
    summary="模型体验对话（SSE）",
    responses=SSE_RESPONSES,
)
async def playground_chat(id: int, body: PlaygroundChatIn) -> None:
    raise NotImplementedError
