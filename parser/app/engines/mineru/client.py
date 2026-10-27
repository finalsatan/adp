"""MinerU V1 接口客户端（知识库技术方案 6.2）。

同步实现，由 :class:`~app.engines.mineru.engine.MineruEngine` 在解析子进程里调用。
一次完整的调用流程：

1. ``POST /v1/uploads`` 创建上传会话（带 sha256，命中已有文件时直接返回 file_id）；
2. 把文件字节 PUT 到服务返回的地址（同源才带 MinerU 密钥，官方云端返回的是
   自带授权的预签名地址，绝不能带）；
3. ``POST /v1/uploads/{id}/complete`` 拿到 file_id；
4. ``POST /v1/parse/jobs`` 异步提交解析作业；
5. ``GET /v1/parse/jobs/{id}`` 轮询到终态；
6. ``GET /v1/files/{file_id}/content`` 下载 ZIP 产物（middle_json、Markdown、images/）。

接口细节以锁定版本（4.0.12）的官方文档和示例脚本 ``scripts/http_api_example.sh``
为准，与本卡描述不一致的地方见卡片的“问题”一节。
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ...errors import ParseError

__all__ = [
    "MAX_FILE_BYTES",
    "MAX_FILE_MIB",
    "MineruClient",
    "ParseArtifacts",
    "read_artifacts",
]

# 单文件上限，与平台默认的单文件上限一致（知识库技术方案 6.2）
MAX_FILE_MIB = 200
MAX_FILE_BYTES = MAX_FILE_MIB * 1024 * 1024

# 作业终态（官方示例脚本 scripts/http_api_example.sh）
TERMINAL_STATUSES = ("completed", "partial", "failed", "canceled")

# 创建上传会话时要带的 MIME 类型
_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".ofd": "application/ofd",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".html": "text/html",
    ".htm": "text/html",
    ".shtml": "text/html",
    ".mhtml": "multipart/related",
    ".mht": "multipart/related",
    ".epub": "application/epub+zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".jp2": "image/jp2",
}

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class ParseArtifacts:
    """一次解析拿到的产物。"""

    middle_json: dict[str, Any]
    markdown: str
    images: dict[str, bytes] = field(default_factory=dict)
    tier: str = ""
    parse_mode: str = ""
    duration_ms: int | None = None
    parser_version: str | None = None


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme.lower(), (parts.hostname or "").lower(), port


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mime_type(suffix: str) -> str:
    return _MIME_TYPES.get(suffix.lower(), "application/octet-stream")


class MineruClient:
    """一个 MinerU V1 服务地址的客户端。"""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        timeout: float = 60.0,
        transfer_timeout: float = 300.0,
        poll_interval: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or None
        self._timeout = timeout
        self._transfer_timeout = transfer_timeout
        self._poll_interval = poll_interval
        self._transport = transport

    # ---- 基础请求 ----
    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def _request(
        self, method: str, path: str, *, auth: bool = True, **kwargs: Any
    ) -> httpx.Response:
        kwargs.setdefault("timeout", self._timeout)
        headers = dict(self._headers()) if auth else {}
        headers.update(kwargs.pop("headers", None) or {})
        headers.setdefault("Accept", "application/json")
        url = self._url(path)
        try:
            with httpx.Client(follow_redirects=True, transport=self._transport) as client:
                return client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ParseError("REMOTE_ERROR", f"连接 MinerU 服务失败：{exc}") from exc

    def _json(self, response: httpx.Response, *, job: bool = False) -> dict[str, Any]:
        if job and response.status_code == 404:
            # 作业只在 mineru 进程内保存，服务重启后查不到（知识库技术方案 6.2）
            raise ParseError("REMOTE_ERROR", "解析服务重启，请重试")
        if response.status_code >= 400:
            raise ParseError("REMOTE_ERROR", _error_message(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise ParseError("REMOTE_ERROR", "MinerU 服务返回的不是 JSON") from exc
        if not isinstance(data, dict):
            raise ParseError("REMOTE_ERROR", "MinerU 服务返回的结构不符合预期")
        return data

    # ---- 健康检查 ----
    def health(self) -> tuple[bool, str | None]:
        """``GET /v1/health``；只用来判断服务是否可用，失败不抛异常。"""
        try:
            response = self._request("GET", "/v1/health")
        except ParseError:
            return False, "连接失败"
        if response.status_code != 200:
            return False, "连接失败"
        return True, None

    # ---- 一次完整解析 ----
    def parse(
        self,
        path: str | Path,
        *,
        tier: str,
        on_progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> ParseArtifacts:
        source = Path(path)
        file_id = self.upload(source)
        job_id = self.submit(file_id, tier)
        job = self.wait(job_id, on_progress=on_progress, cancel_event=cancel_event)
        return read_artifacts(self.download_zip(job))

    def upload(self, path: Path) -> str:
        """上传文件，返回 file_id。"""
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ParseError(
                "FILE_TOO_LARGE",
                f"文件超过 MinerU 单文件上限 {MAX_FILE_MIB} MiB",
            )
        payload = {
            "filename": path.name,
            "bytes": size,
            "mime_type": _mime_type(path.suffix),
            "purpose": "parse",
            "sha256sum": _sha256(path),
        }
        created = self._json(self._request("POST", "/v1/uploads", json=payload))
        if created.get("status") == "completed":
            return _file_id(created)
        upload_url = created.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url:
            raise ParseError("REMOTE_ERROR", "MinerU 未返回上传地址")

        # 官方云端返回预签名地址（不同源，不能带密钥）；自建服务同源，要带密钥
        headers = dict(created.get("upload_headers") or {})
        if self.api_key and _origin(urljoin(self.base_url + "/", upload_url)) == _origin(
            self.base_url
        ):
            headers["Authorization"] = f"Bearer {self.api_key}"
        method = created.get("upload_method") or "PUT"
        with path.open("rb") as handle:
            response = self._request(
                method,
                upload_url,
                auth=False,
                content=handle,
                headers=headers,
                timeout=self._transfer_timeout,
            )
        if response.status_code >= 400:
            raise ParseError("REMOTE_ERROR", _error_message(response))

        completed = self._json(self._request("POST", f"/v1/uploads/{_upload_id(created)}/complete"))
        return _file_id(completed)

    def submit(self, file_id: str, tier: str) -> str:
        """异步提交解析作业，返回 job_id。ZIP 产物里有 middle_json、Markdown 和 images/。"""
        payload = {
            "files": [{"source": {"type": "file_id", "file_id": file_id}}],
            "tier": tier,
            "output_formats": ["zip"],
        }
        data = self._json(self._request("POST", "/v1/parse/jobs", json=payload))
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise ParseError("REMOTE_ERROR", "MinerU 未返回作业 ID")
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._json(self._request("GET", f"/v1/parse/jobs/{job_id}"), job=True)

    def wait(
        self,
        job_id: str,
        *,
        on_progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """轮询到终态；同时把进度报给调用方（X01 的进度回调）。"""
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ParseError("CANCELLED")
            job = self.get_job(job_id)
            progress = job.get("progress") or {}
            if on_progress is not None:
                on_progress(
                    str(job.get("status") or ""),
                    int(progress.get("completed") or 0),
                    int(progress.get("total") or 0),
                )
            status = job.get("status")
            if status in TERMINAL_STATUSES:
                break
            if cancel_event is not None:
                if cancel_event.wait(self._poll_interval):
                    raise ParseError("CANCELLED")
            else:
                time.sleep(self._poll_interval)

        if status == "failed":
            raise ParseError("REMOTE_ERROR", f"远程解析失败：{_job_error(job)}")
        if status == "partial":
            raise ParseError("REMOTE_ERROR", f"远程解析只完成了部分内容：{_job_error(job)}")
        if status == "canceled":
            raise ParseError("REMOTE_ERROR", "远程解析任务已取消")
        return job

    def download_zip(self, job: dict[str, Any]) -> bytes:
        files = job.get("files") or []
        for entry in files:
            output_files = entry.get("output_files") or {}
            reference = output_files.get("zip")
            if reference and reference.get("file_id"):
                return self._download(reference["file_id"])
        raise ParseError("REMOTE_ERROR", "MinerU 未返回 ZIP 解析结果")

    def _download(self, file_id: str) -> bytes:
        response = self._request(
            "GET",
            f"/v1/files/{file_id}/content",
            timeout=self._transfer_timeout,
            headers={"Accept": "application/zip, application/octet-stream"},
        )
        if response.status_code >= 400:
            raise ParseError("REMOTE_ERROR", _error_message(response))
        return response.content


def read_artifacts(zip_bytes: bytes) -> ParseArtifacts:
    """解出 ZIP 里的 middle_json、Markdown 和 images/。"""
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ParseError("REMOTE_ERROR", "MinerU 返回的 ZIP 结果无法解压") from exc

    with archive:
        names = archive.namelist()
        middle_name = _find(names, "middle_json.json")
        if middle_name is None:
            raise ParseError("REMOTE_ERROR", "MinerU 的 ZIP 结果里没有 middle_json.json")
        try:
            middle = json.loads(archive.read(middle_name))
        except (ValueError, KeyError) as exc:
            raise ParseError("REMOTE_ERROR", "MinerU 的 middle_json 不是合法 JSON") from exc
        markdown_name = _find(names, "markdown.md")
        markdown = archive.read(markdown_name).decode("utf-8", "replace") if markdown_name else ""
        images = {
            name: archive.read(name)
            for name in names
            if name.startswith("images/") and not name.endswith("/")
        }

    extensions = middle.get("extensions") or {}
    mineru = extensions.get("mineru") or {}
    return ParseArtifacts(
        middle_json=middle,
        markdown=markdown,
        images=images,
        tier=str(mineru.get("tier") or ""),
        parse_mode=str(mineru.get("parse_mode") or ""),
        parser_version=str((middle.get("metadata") or {}).get("producer", {}).get("version") or ""),
    )


def _find(names: list[str], target: str) -> str | None:
    for name in names:
        if name == target or name.endswith(f"/{target}"):
            return name
    return None


def _upload_id(data: dict[str, Any]) -> str:
    upload_id = data.get("id")
    if not isinstance(upload_id, str) or not upload_id:
        raise ParseError("REMOTE_ERROR", "MinerU 未返回上传会话 ID")
    return upload_id


def _file_id(data: dict[str, Any]) -> str:
    file_object = data.get("file") or {}
    file_id = file_object.get("id")
    if not isinstance(file_id, str) or not file_id:
        raise ParseError("REMOTE_ERROR", "MinerU 未返回文件 ID")
    return file_id


def _job_error(job: dict[str, Any]) -> str:
    for entry in job.get("files") or []:
        error = entry.get("error") or {}
        if error:
            return str(error.get("message") or error.get("code") or "未知原因")
    return str(job.get("status") or "未知原因")


def _error_message(response: httpx.Response) -> str:
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")
        detail = detail or str(body.get("detail") or body.get("message") or "")
    if not detail:
        detail = response.text[:200]
    return f"MinerU 服务返回 {response.status_code}：{detail}"
