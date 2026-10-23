"""模型接口的枚举、请求和返回结构（P2-01 契约，实现见 P2-03/P2-06/P2-07）。"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.schemas import OrgBrief, RequestModel, UserBrief
from app.gateway.events import ModelCallErrorCode


class ModelType(StrEnum):
    """模型类型。"""

    chat = "chat"
    embedding = "embedding"
    rerank = "rerank"


class ModelStatus(StrEnum):
    """模型状态。"""

    enabled = "enabled"
    disabled = "disabled"


def _check_litellm_model(value: str) -> str:
    """模型 ID 需形如 `供应商/模型`（含 `/`，前后不能是空段）。"""
    if "/" not in value or value.startswith("/") or value.endswith("/"):
        raise ValueError("模型 ID 需形如 供应商/模型")
    return value


class RefCounts(BaseModel):
    """模型被各类资源引用的数量。"""

    app: int = 0
    kb: int = 0
    scheme: int = 0


class ModelOut(BaseModel):
    """模型详情。不含任何密钥字段。"""

    id: int
    name: str
    model_type: ModelType
    litellm_model: str
    api_base: str | None
    api_key_last4: str | None
    description: str | None
    context_length: int | None
    max_output: int | None
    cap_vision: bool
    cap_tools: bool
    cap_thinking: bool
    embedding_dim: int | None
    embedding_max_input: int | None
    embedding_batch_size: int | None
    default_params: dict[str, Any]
    first_token_timeout_s: int
    total_timeout_s: int
    max_retries: int
    rate_qps: int | None
    rate_concurrency: int | None
    status: ModelStatus
    ref_counts: RefCounts
    fields_locked: bool
    last_test_at: datetime | None
    last_test_ok: bool | None
    last_test_message: str | None
    created_at: datetime
    updated_at: datetime


class ModelCreate(RequestModel):
    """新建模型。按类型检查必填字段：对话要上下文长度和最大输出，向量化要维度三项。"""

    name: str = Field(min_length=1, max_length=50)
    model_type: ModelType
    litellm_model: str
    api_base: str | None = None
    api_key: str | None = None
    description: str | None = None
    context_length: int | None = None
    max_output: int | None = None
    cap_vision: bool = False
    cap_tools: bool = False
    cap_thinking: bool = False
    embedding_dim: int | None = Field(default=None, ge=1, le=4000)
    embedding_max_input: int | None = None
    embedding_batch_size: int | None = None
    default_params: dict[str, Any] = Field(default_factory=dict)
    first_token_timeout_s: int = 30
    total_timeout_s: int = 300
    max_retries: int = Field(default=2, ge=0, le=5)
    rate_qps: int | None = None
    rate_concurrency: int | None = None

    @field_validator("litellm_model")
    @classmethod
    def _validate_litellm_model(cls, value: str) -> str:
        return _check_litellm_model(value)

    @model_validator(mode="after")
    def _require_fields_by_type(self) -> "ModelCreate":
        if self.model_type == ModelType.chat:
            required = ("context_length", "max_output")
        elif self.model_type == ModelType.embedding:
            required = ("embedding_dim", "embedding_max_input", "embedding_batch_size")
        else:
            required = ()
        missing = [name for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(f"缺少该类型需要的字段：{', '.join(missing)}")
        return self


class ModelUpdate(RequestModel):
    """编辑模型。除 `model_type` 外都可选；`api_key` 为空或省略表示沿用原密钥。

    `model_type` 保留在结构里，由实现返回 422 `MODEL_TYPE_IMMUTABLE`（P2-03）。
    """

    name: str | None = Field(default=None, min_length=1, max_length=50)
    model_type: ModelType | None = None
    litellm_model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    description: str | None = None
    context_length: int | None = None
    max_output: int | None = None
    cap_vision: bool | None = None
    cap_tools: bool | None = None
    cap_thinking: bool | None = None
    embedding_dim: int | None = Field(default=None, ge=1, le=4000)
    embedding_max_input: int | None = None
    embedding_batch_size: int | None = None
    default_params: dict[str, Any] | None = None
    first_token_timeout_s: int | None = None
    total_timeout_s: int | None = None
    max_retries: int | None = Field(default=None, ge=0, le=5)
    rate_qps: int | None = None
    rate_concurrency: int | None = None

    @field_validator("litellm_model")
    @classmethod
    def _validate_litellm_model(cls, value: str | None) -> str | None:
        return _check_litellm_model(value) if value is not None else None


class ModelRefOut(BaseModel):
    """引用模型的资源（停用确认弹窗用）。"""

    resource_id: int
    type: str
    type_label: str
    name: str
    owner: UserBrief
    org: OrgBrief


class ModelTestIn(ModelCreate):
    """保存前的连通测试：用表单参数，可带 model_id 以复用已保存的密钥。"""

    model_id: int | None = None


class ModelTestResult(BaseModel):
    """连通测试结果。调用失败也是 200 + ok=false。"""

    ok: bool
    latency_ms: int
    first_token_ms: int | None = None
    reply: str | None = None
    returned_model: str | None = None
    dims: int | None = None
    error_code: ModelCallErrorCode | None = None
    error_message: str | None = None
    tested_at: datetime


class PlaygroundMessage(BaseModel):
    """模型体验的一条消息。"""

    role: Literal["user", "assistant"]
    content: str


class PlaygroundParams(RequestModel):
    """模型体验的本次参数，覆盖模型默认值。"""

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    thinking: bool | None = None


class PlaygroundChatIn(RequestModel):
    """模型体验对话请求。消息历史由前端随请求带上，不落库。"""

    messages: list[PlaygroundMessage] = Field(min_length=1, max_length=100)
    params: PlaygroundParams = Field(default_factory=PlaygroundParams)
