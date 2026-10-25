"""解析服务 HTTP 客户端：知识库技术方案 6.1。

只做请求封装与错误映射（超时、非 2xx 转成 `ParserUnavailableError`）；
轮询等待、进度写回、重试留在 P3-09 的流水线里。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ParserUnavailableError

# 同步转换 `POST /v1/convert` 超时 120 秒（知识库技术方案 6.1）。
DEFAULT_TIMEOUT_SECONDS = 120.0


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
