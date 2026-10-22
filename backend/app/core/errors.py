"""错误码与统一错误返回。错误码只在这里定义。"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    status: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "服务器内部错误"

    def __init__(self, message: str | None = None, detail: dict[str, Any] | None = None):
        if message is not None:
            self.message = message
        self.detail = detail
        super().__init__(self.message)


class ValidationError(AppError):
    status = 422
    code = "VALIDATION_ERROR"
    message = "请求参数不合法"


class NotFoundError(AppError):
    status = 404
    code = "NOT_FOUND"
    message = "资源不存在"


class NotImplementedAppError(AppError):
    status = 501
    code = "NOT_IMPLEMENTED"
    message = "功能尚未实现"


class InternalError(AppError):
    status = 500
    code = "INTERNAL_ERROR"
    message = "服务器内部错误"


def _resolve_request_id(request: Request) -> str | None:
    """优先取中间件写在 scope.state 上的请求 ID。

    未捕获异常由最外层的 ServerErrorMiddleware 处理，此时请求 ID 中间件的
    contextvar 已经 reset，所以还要看 request.state。
    """
    return getattr(request.state, "request_id", None) or request_id_var.get()


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = _resolve_request_id(request)
    # 未捕获异常时响应不经过请求 ID 中间件，头在这里补上，保证和 body 一致
    headers = {"X-Request-Id": request_id} if request_id else None
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "detail": detail,
            "request_id": request_id,
        },
        headers=headers,
    )


_HTTP_CODES = {
    400: ("BAD_REQUEST", "请求不合法"),
    404: (NotFoundError.code, NotFoundError.message),
    405: ("METHOD_NOT_ALLOWED", "不支持的请求方法"),
}


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return error_response(request, exc.status, exc.code, exc.message, exc.detail)


async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{k: v for k, v in err.items() if k in ("type", "loc", "msg")} for err in exc.errors()]
    return error_response(
        request,
        ValidationError.status,
        ValidationError.code,
        ValidationError.message,
        {"errors": errors},
    )


async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = _HTTP_CODES.get(exc.status_code, ("HTTP_ERROR", "请求失败"))
    return error_response(request, exc.status_code, code, message)


async def _not_implemented_handler(request: Request, exc: NotImplementedError) -> JSONResponse:
    e = NotImplementedAppError
    return error_response(request, e.status, e.code, e.message)


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception", exc_info=exc)
    e = InternalError
    return error_response(request, e.status, e.code, e.message)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(NotImplementedError, _not_implemented_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
