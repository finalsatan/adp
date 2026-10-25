"""解析服务 HTTP 客户端：知识库技术方案 6.1。

P3-02 只做请求封装与错误映射（超时、非 2xx 转成 `ParserUnavailableError`）；
P3-09 在其上补流水线需要的等待：轮询 `GET /v1/jobs/{id}`、作业 404 时重新提交、
等待超过作业超时按临时错误处理。进度写回、重试策略仍在流水线里。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ParserUnavailableError

# 同步转换 `POST /v1/convert` 超时 120 秒（知识库技术方案 6.1）。
DEFAULT_TIMEOUT_SECONDS = 120.0
# 轮询间隔与单个作业的等待超时（知识库技术方案 6.3：单个作业超时默认 30 分钟）。
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_JOB_TIMEOUT_SECONDS = 1800.0
# 作业的终态（知识库技术方案 6.1）。
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

JobStatusCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ParserJobTimeout(Exception):
    """等待解析作业超过超时时间（知识库技术方案 6.3），按临时错误处理。"""

    def __init__(self, job_id: str | None = None) -> None:
        self.job_id = job_id
        super().__init__("等待解析作业超时")


def _is_job_not_found(exc: ParserUnavailableError) -> bool:
    """作业 404：解析服务重启后作业丢失，调用方应重新提交（6.1）。"""
    detail = exc.detail or {}
    return detail.get("status_code") == 404


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        code = payload.get("code")
        if isinstance(code, str):
            return code
    return None


class ParserClient:
    """每个请求带 `Authorization: Bearer {PARSER_TOKEN}`（知识库技术方案 6.1）。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        headers = {}
        if settings.PARSER_TOKEN:
            headers["Authorization"] = f"Bearer {settings.PARSER_TOKEN}"
        self._client = httpx.AsyncClient(
            base_url=settings.PARSER_URL.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            # 只带异常类型，不带请求头 / 令牌（开发约定 4.5）
            raise ParserUnavailableError(f"解析服务请求失败（{type(exc).__name__}）") from exc
        if response.status_code >= 400:
            detail: dict[str, Any] = {"status_code": response.status_code}
            code = _error_code(response)
            if code:
                detail["code"] = code
            raise ParserUnavailableError(f"解析服务返回 HTTP {response.status_code}", detail=detail)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ParserUnavailableError("解析服务返回的内容不是合法 JSON") from exc

    async def wait_job(
        self,
        payload: dict[str, Any],
        *,
        on_status: JobStatusCallback | None = None,
        interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        timeout: float = DEFAULT_JOB_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """提交作业并轮询到终态。

        - 每次轮询调用 `on_status(status)`，调用方据此写回进度与心跳（可以抛异常取消）。
        - 查询作业返回 404 时重新提交（解析服务重启后作业只在内存里）。
        - 等待超过 `timeout` 抛 `ParserJobTimeout`，由调用方按临时错误重试。
        """
        job_id: str | None = None
        deadline = time.monotonic() + timeout
        while True:
            if job_id is None:
                created = await self.submit_job(payload)
                job_id = str(created["id"])
            try:
                status = await self.get_job(job_id)
            except ParserUnavailableError as exc:
                if _is_job_not_found(exc):
                    # 作业丢失，重新提交后继续等（6.1）；一直 404 也要受超时约束。
                    job_id = None
                    if time.monotonic() >= deadline:
                        raise ParserJobTimeout(None) from exc
                    continue
                raise
            if on_status is not None:
                await on_status(status)
            if status.get("status") in TERMINAL_JOB_STATUSES:
                return status
            if time.monotonic() >= deadline:
                raise ParserJobTimeout(job_id)
            await asyncio.sleep(interval)

    async def submit_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v1/jobs", json=payload)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/jobs/{job_id}")

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v1/jobs/{job_id}")

    async def list_engines(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v1/engines")

    async def convert(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v1/convert", json=payload)

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/healthz")
