"""解析服务的错误码与异常。

错误码与知识库技术方案 6.1、X01 卡片一致。``message`` 面向最终用户，用中文。
"""

from __future__ import annotations

from typing import Any

# 作业错误码（返回给 worker 的 error.code）
ERROR_MESSAGES: dict[str, str] = {
    "UNSUPPORTED_TYPE": "不支持的文件类型",
    "ENGINE_UNAVAILABLE": "解析引擎不可用",
    "FILE_CORRUPTED": "文件已损坏，无法解析",
    "FILE_ENCRYPTED": "文件已加密，无法解析",
    "FILE_TYPE_MISMATCH": "文件内容与扩展名不一致",
    "FILE_TOO_LARGE": "文件超过解析引擎的大小上限",
    "ARCHIVE_TOO_LARGE": "压缩包解压后过大，已拒绝解析",
    "TOO_MANY_PAGES": "文档页数超过上限",
    "TIMEOUT": "解析超时，请稍后重试或换用更小的文档",
    "CANCELLED": "作业已取消",
    "REMOTE_ERROR": "远程解析服务调用失败",
    "ENGINE_ERROR": "解析引擎执行失败",
    "INVALID_PARAMS": "解析参数不正确",
    # 接口级错误码
    "JOB_NOT_FOUND": "作业不存在",
    "UNAUTHORIZED": "认证失败",
    "QUEUE_FULL": "解析服务繁忙，请稍后重试",
}


def message_for(code: str, default: str = "解析失败") -> str:
    return ERROR_MESSAGES.get(code, default)


class ParseError(Exception):
    """引擎或作业执行失败，携带错误码和面向用户的中文说明。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or message_for(code)
        super().__init__(self.message)


class AppError(Exception):
    """HTTP 接口错误，由 main.py 统一转换成标准错误响应。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message or message_for(code)
        self.detail = detail
        super().__init__(self.message)
