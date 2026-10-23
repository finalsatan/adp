"""LiteLLM 异常到模型调用错误代码的映射（技术方案 9.3，错误码见 P2-01）。

只在这里把上游异常翻译成 `ModelCallErrorCode` 和中文说明，`service.py` 只管重试。
可重试的错误：超时、连接失败、供应商 5xx、供应商 429；密钥无效、模型不存在、
请求参数错误、上下文超长不重试（技术方案 9.3）。
"""

import asyncio

import litellm

from app.gateway.events import ModelCallErrorCode

# 重试前必须满足“还没收到任何片段”，这里只判断错误本身是否可重试。
RETRYABLE_CODES = frozenset(
    {
        ModelCallErrorCode.FIRST_TOKEN_TIMEOUT.value,
        ModelCallErrorCode.CONNECT_ERROR.value,
        ModelCallErrorCode.UPSTREAM_ERROR.value,
        ModelCallErrorCode.UPSTREAM_RATE_LIMITED.value,
    }
)


class ModelCallError(Exception):
    """一次模型调用最终失败。`error_code` 是 P2-01 的调用错误代码。"""

    def __init__(self, error_code: ModelCallErrorCode | str, message: str) -> None:
        self.error_code = (
            error_code.value if isinstance(error_code, ModelCallErrorCode) else error_code
        )
        self.message = message
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        return self.error_code in RETRYABLE_CODES


def map_exception(exc: BaseException) -> ModelCallError:
    """把 LiteLLM / httpx / asyncio 的异常映射成 `ModelCallError`。"""
    if isinstance(exc, ModelCallError):
        return exc

    # ContextWindowExceededError 是 BadRequestError 的子类，必须先判断。
    if isinstance(exc, litellm.ContextWindowExceededError):
        return ModelCallError(ModelCallErrorCode.CONTEXT_TOO_LONG, "请求超出模型上下文长度")
    if isinstance(exc, (litellm.AuthenticationError, litellm.PermissionDeniedError)):
        return ModelCallError(ModelCallErrorCode.UPSTREAM_AUTH, "密钥无效")
    if isinstance(exc, litellm.NotFoundError):
        return ModelCallError(ModelCallErrorCode.UPSTREAM_NOT_FOUND, "模型不存在")
    if isinstance(exc, litellm.RateLimitError):
        return ModelCallError(ModelCallErrorCode.UPSTREAM_RATE_LIMITED, "供应商限流，请稍后再试")
    # Timeout 是 APIConnectionError 的子类，必须先判断。
    if isinstance(exc, (litellm.Timeout, asyncio.TimeoutError, TimeoutError)):
        return ModelCallError(ModelCallErrorCode.FIRST_TOKEN_TIMEOUT, "连接超时")
    if isinstance(exc, litellm.APIConnectionError):
        return ModelCallError(ModelCallErrorCode.CONNECT_ERROR, "连接失败")
    if isinstance(
        exc,
        (
            litellm.InternalServerError,
            litellm.ServiceUnavailableError,
            litellm.BadGatewayError,
        ),
    ):
        return ModelCallError(ModelCallErrorCode.UPSTREAM_ERROR, "供应商服务异常")
    if isinstance(
        exc,
        (litellm.BadRequestError, litellm.UnprocessableEntityError, litellm.UnsupportedParamsError),
    ):
        return ModelCallError(ModelCallErrorCode.UPSTREAM_BAD_REQUEST, "请求参数不被接受")
    if isinstance(exc, litellm.APIError):
        return ModelCallError(ModelCallErrorCode.UPSTREAM_ERROR, "模型调用失败")
    return ModelCallError(ModelCallErrorCode.UPSTREAM_ERROR, "模型调用失败")
