"""错误码与统一错误返回。错误码只在这里定义。"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.audit.deps import audit_failure
from app.core.context import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    status: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "服务器内部错误"
    # 刷新令牌失效时置 True，错误响应里一并清除两个认证 Cookie。
    clear_auth_cookies: bool = False

    def __init__(self, message: str | None = None, detail: dict[str, Any] | None = None):
        if message is not None:
            self.message = message
        self.detail = detail
        super().__init__(self.message)


class ValidationError(AppError):
    status = 422
    code = "VALIDATION_ERROR"
    message = "请求参数不正确"


class NotFoundError(AppError):
    status = 404
    code = "NOT_FOUND"
    message = "对象不存在或已删除"


class NotImplementedAppError(AppError):
    status = 501
    code = "NOT_IMPLEMENTED"
    message = "功能尚未实现"


class InternalError(AppError):
    status = 500
    code = "INTERNAL_ERROR"
    message = "服务器内部错误"


# ---- 第一期业务错误码（P1-03）----


class AuthRequiredError(AppError):
    status = 401
    code = "AUTH_REQUIRED"
    message = "请先登录"


class InvalidCredentialsError(AppError):
    status = 401
    code = "INVALID_CREDENTIALS"
    message = "用户名或密码错误"


class LoginLockedError(AppError):
    status = 429
    code = "LOGIN_LOCKED"
    message = "尝试次数过多，请 {n} 分钟后再试"

    def __init__(self, minutes: int) -> None:
        super().__init__(self.message.format(n=minutes), {"minutes": minutes})


class AccountDisabledError(AppError):
    status = 403
    code = "ACCOUNT_DISABLED"
    message = "账号已停用，请联系管理员"


class RefreshInvalidError(AppError):
    status = 401
    code = "REFRESH_INVALID"
    message = "登录已过期，请重新登录"
    clear_auth_cookies = True


class PasswordChangeRequiredError(AppError):
    status = 403
    code = "PASSWORD_CHANGE_REQUIRED"
    message = "请先修改密码"


class WrongOldPasswordError(AppError):
    status = 400
    code = "WRONG_OLD_PASSWORD"
    message = "原密码不正确"


class WeakPasswordError(AppError):
    status = 422
    code = "WEAK_PASSWORD"
    message = "密码不符合要求"

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(self.message, {"reasons": reasons})


class PermissionDeniedError(AppError):
    status = 403
    code = "PERMISSION_DENIED"
    message = "没有权限执行该操作"


class NameConflictError(AppError):
    status = 409
    code = "NAME_CONFLICT"
    message = "名称已存在"


class UsernameExistsError(AppError):
    status = 409
    code = "USERNAME_EXISTS"
    message = "用户名已存在"


class OrgNotEmptyError(AppError):
    status = 409
    code = "ORG_NOT_EMPTY"
    message = "组织下还有下级组织、用户或资源，不能删除"

    def __init__(self, children: int, users: int, resources: int) -> None:
        super().__init__(
            self.message,
            {"children": children, "users": users, "resources": resources},
        )


class OrgRequiredError(AppError):
    status = 422
    code = "ORG_REQUIRED"
    message = "请选择所属组织"


class LastSysAdminError(AppError):
    status = 409
    code = "LAST_SYS_ADMIN"
    message = "至少要保留一个启用的系统管理员"


class CannotTargetSelfError(AppError):
    status = 422
    code = "CANNOT_TARGET_SELF"
    message = "不能对自己执行该操作"


class ResourceInUseError(AppError):
    status = 409
    code = "RESOURCE_IN_USE"
    message = "被其他资源引用，请先解除引用"

    def __init__(self, refs: list[dict[str, Any]]) -> None:
        super().__init__(self.message, {"refs": refs})


class VisibilityFixedError(AppError):
    status = 422
    code = "VISIBILITY_FIXED"
    message = "该类资源的可见范围不能修改"


class AlreadyMemberError(AppError):
    status = 409
    code = "ALREADY_MEMBER"
    message = "该用户已经是协作者"


# ---- 第二期业务错误码（P2-01）----


class ModelUnavailableError(AppError):
    status = 409
    code = "MODEL_UNAVAILABLE"
    message = "模型不可用（已停用或已删除）"


class ModelRateLimitedError(AppError):
    status = 429
    code = "MODEL_RATE_LIMITED"
    message = "当前调用的人较多，请稍后再试"


class ModelFieldLockedError(AppError):
    status = 409
    code = "MODEL_FIELD_LOCKED"
    message = "该向量化模型已被知识库引用，模型 ID 和向量维度不能修改"


class ModelTypeImmutableError(AppError):
    status = 422
    code = "MODEL_TYPE_IMMUTABLE"
    message = "模型类型创建后不能修改"


class ModelTypeMismatchError(AppError):
    status = 422
    code = "MODEL_TYPE_MISMATCH"
    message = "该模型的类型不支持这个操作（如对向量化模型发起对话）"


class ModelHostNotAllowedError(AppError):
    status = 422
    code = "MODEL_HOST_NOT_ALLOWED"
    message = "接口地址指向内网或本机，不允许使用；如需使用请在系统设置中加入白名单"


class ModelCallFailedError(AppError):
    status = 502
    code = "MODEL_CALL_FAILED"
    message = "模型调用失败"

    def __init__(self, error_code: str, message: str | None = None) -> None:
        super().__init__(message or self.message, {"error_code": error_code})


class InvalidTimeRangeError(AppError):
    status = 422
    code = "INVALID_TIME_RANGE"
    message = "时间范围不正确或超过 366 天"


# ---- 第三期业务错误码（P3-01）----


class KbKindImmutableError(AppError):
    status = 422
    code = "KB_KIND_IMMUTABLE"
    message = "知识库类型创建后不能修改"


class KbEmbeddingModelImmutableError(AppError):
    status = 422
    code = "KB_EMBEDDING_MODEL_IMMUTABLE"
    message = "向量化模型创建后不能修改"


class EmbeddingModelUnavailableError(AppError):
    status = 409
    code = "EMBEDDING_MODEL_UNAVAILABLE"
    message = "向量化模型不可用（已停用或已删除）"


class FolderHasChildrenError(AppError):
    status = 409
    code = "FOLDER_HAS_CHILDREN"
    message = "文件夹下还有下级文件夹，不能删除"


class FolderNotEmptyError(AppError):
    status = 409
    code = "FOLDER_NOT_EMPTY"
    message = "文件夹下还有文档，不能删除"


class MetadataValueInvalidError(AppError):
    status = 422
    code = "METADATA_VALUE_INVALID"
    message = "元数据字段的值不符合字段类型"


class UnsupportedFileTypeError(AppError):
    status = 422
    code = "UNSUPPORTED_FILE_TYPE"
    message = "不支持的文件类型"


class FileTooLargeError(AppError):
    status = 413
    code = "FILE_TOO_LARGE"
    message = "文件超过大小上限"


class EngineUnavailableError(AppError):
    status = 422
    code = "ENGINE_UNAVAILABLE"
    message = "解析引擎不可用或未配置"


class SchemeTypeMismatchError(AppError):
    status = 422
    code = "SCHEME_TYPE_MISMATCH"
    message = "入库方案与文件类型不匹配"


class SchemeUnavailableError(AppError):
    status = 422
    code = "SCHEME_UNAVAILABLE"
    message = "入库方案不可用"


class ChunkTooLongError(AppError):
    status = 422
    code = "CHUNK_TOO_LONG"
    message = "分块超过向量化模型的最大输入长度"


class DocumentProcessingError(AppError):
    status = 409
    code = "DOCUMENT_PROCESSING"
    message = "文档正在处理中，暂不能执行该操作"


class DocumentNotReadyError(AppError):
    status = 409
    code = "DOCUMENT_NOT_READY"
    message = "文档尚未完成入库"


class ParserUnavailableError(AppError):
    status = 502
    code = "PARSER_UNAVAILABLE"
    message = "解析服务不可用"


class StorageError(AppError):
    status = 502
    code = "STORAGE_ERROR"
    message = "对象存储读写失败"


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
    # 带 audited 的路由失败时也记一条审计（未登录的 401 不记）。
    await audit_failure(request, exc.code)
    response = error_response(request, exc.status, exc.code, exc.message, exc.detail)
    if exc.clear_auth_cookies:
        # 延迟导入，避免 core -> auth 的模块级循环依赖。
        from app.auth.cookies import clear_auth_cookies

        clear_auth_cookies(response)
    return response


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
