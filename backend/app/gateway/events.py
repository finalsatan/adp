"""模型体验（SSE）的事件结构（技术方案 9.6，D9）。

每条事件只用 `data:` 一行，内容是带 `type` 字段的 JSON；心跳是注释行 `: ping`。
事件模型用判别联合 `SseEvent`；SSE 内容不会自动进 OpenAPI，由 `main.py` 的自定义
`app.openapi()` 把 `sse_openapi_schemas()` 合并进 components。前端遇到不认识的
`type` 直接忽略，保证向前兼容（第四期会加 `STEP_STARTED` 等）。
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class ModelCallErrorCode(StrEnum):
    """模型调用错误代码：记在 `model_usage.error_code`，也用于 RUN_ERROR 和连通测试。"""

    UPSTREAM_AUTH = "UPSTREAM_AUTH"
    UPSTREAM_NOT_FOUND = "UPSTREAM_NOT_FOUND"
    UPSTREAM_BAD_REQUEST = "UPSTREAM_BAD_REQUEST"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    CONNECT_ERROR = "CONNECT_ERROR"
    FIRST_TOKEN_TIMEOUT = "FIRST_TOKEN_TIMEOUT"
    TOTAL_TIMEOUT = "TOTAL_TIMEOUT"
    CONTEXT_TOO_LONG = "CONTEXT_TOO_LONG"
    CANCELLED = "CANCELLED"
    DIM_MISMATCH = "DIM_MISMATCH"


class RunStartedEvent(BaseModel):
    """开始，带 run_id。"""

    type: Literal["RUN_STARTED"] = "RUN_STARTED"
    run_id: str


class TextDeltaEvent(BaseModel):
    """文本片段。"""

    type: Literal["TEXT_DELTA"] = "TEXT_DELTA"
    delta: str


class ReasoningDeltaEvent(BaseModel):
    """深度思考内容片段。"""

    type: Literal["REASONING_DELTA"] = "REASONING_DELTA"
    delta: str


class RunFinishedEvent(BaseModel):
    """结束，带输入输出 token 数、首字耗时、总耗时。"""

    type: Literal["RUN_FINISHED"] = "RUN_FINISHED"
    input_tokens: int
    output_tokens: int
    first_token_ms: int
    total_ms: int


class RunErrorEvent(BaseModel):
    """出错，code 为调用错误代码或 `MODEL_UNAVAILABLE`、`MODEL_RATE_LIMITED`。"""

    type: Literal["RUN_ERROR"] = "RUN_ERROR"
    code: str
    message: str


SseEvent = Annotated[
    RunStartedEvent | TextDeltaEvent | ReasoningDeltaEvent | RunFinishedEvent | RunErrorEvent,
    Field(discriminator="type"),
]


def sse_openapi_schemas() -> dict[str, Any]:
    """生成 SSE 事件的 OpenAPI 结构，供自定义 `app.openapi()` 合并进 components。"""
    schema = TypeAdapter(SseEvent).json_schema(ref_template="#/components/schemas/{model}")
    defs = schema.pop("$defs", {})
    return {**defs, "SseEvent": schema}
