"""对话 SSE 的事件结构（应用技术方案 9.1，P4-01 契约）。

在 P2-01 的 `app/gateway/events.py` 事件格式上增加对话专用事件。为避免改动 P2-01
的 `SseEvent` 契约，`RUN_STARTED`、`RUN_FINISHED` 另建模型（`type` 取值相同，但在
`ChatSseEvent` 里各自映射）；`TEXT_DELTA`、`REASONING_DELTA`、`RUN_ERROR` 复用模型
体验的模型。SSE 内容不会自动进 OpenAPI，由 `main.py` 的自定义 `app.openapi()` 把
`chat_sse_openapi_schemas()` 合并进 components。
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from app.chat.schemas import AnswerKind, AnswerStatus, CitationOut, StepName, TraceOut
from app.gateway.events import ReasoningDeltaEvent, RunErrorEvent, TextDeltaEvent


class ChatRunStartedEvent(BaseModel):
    """开始，带会话、轮次与回答 ID（新会话第一问时前端由此拿到会话 ID）。"""

    type: Literal["RUN_STARTED"] = "RUN_STARTED"
    run_id: str
    conversation_id: int
    turn_id: int
    answer_id: int


class StepStartedEvent(BaseModel):
    """一个处理步骤开始。"""

    type: Literal["STEP_STARTED"] = "STEP_STARTED"
    step: StepName


class StepFinishedEvent(BaseModel):
    """一个处理步骤结束。"""

    type: Literal["STEP_FINISHED"] = "STEP_FINISHED"
    step: StepName
    duration_ms: int


class CitationsEvent(BaseModel):
    """本次全部参考内容（不含位置，点击时再取预览）。"""

    type: Literal["CITATIONS"] = "CITATIONS"
    citations: list[CitationOut]


class SuggestionsEvent(BaseModel):
    """追问建议（第六期生效，事件结构先保留）。"""

    type: Literal["SUGGESTIONS"] = "SUGGESTIONS"
    suggestions: list[str]


class TraceEvent(BaseModel):
    """完整处理过程，只在调试预览中发出。"""

    type: Literal["TRACE"] = "TRACE"
    trace: TraceOut


class ChatRunFinishedEvent(BaseModel):
    """结束，带回答类型、状态与用量。"""

    type: Literal["RUN_FINISHED"] = "RUN_FINISHED"
    kind: AnswerKind
    status: AnswerStatus
    input_tokens: int
    output_tokens: int
    first_token_ms: int
    total_ms: int


ChatSseEvent = Annotated[
    ChatRunStartedEvent
    | StepStartedEvent
    | StepFinishedEvent
    | CitationsEvent
    | TextDeltaEvent
    | ReasoningDeltaEvent
    | SuggestionsEvent
    | TraceEvent
    | ChatRunFinishedEvent
    | RunErrorEvent,
    Field(discriminator="type"),
]


def chat_sse_openapi_schemas() -> dict[str, Any]:
    """生成对话 SSE 事件的 OpenAPI 结构，供自定义 `app.openapi()` 合并进 components。"""
    schema = TypeAdapter(ChatSseEvent).json_schema(ref_template="#/components/schemas/{model}")
    defs = schema.pop("$defs", {})
    return {**defs, "ChatSseEvent": schema}
