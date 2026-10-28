"""测试用的假 MinerU V1 服务（真 HTTP，跑在 127.0.0.1 的随机端口）。

解析子进程没法共用 ``httpx.MockTransport``，所以走真实 HTTP 的测试（引擎和
``POST /v1/jobs`` 接口）用这个假服务。它实现了适配器用到的全部接口：

- ``GET /v1/health``
- ``POST /v1/uploads``、``PUT <upload_url>``、``POST /v1/uploads/{id}/complete``
- ``POST /v1/parse/jobs``、``GET /v1/parse/jobs/{id}``
- ``GET /v1/files/{id}/content``（返回 ZIP：middle_json.json、markdown.md、images/）

``upload_url`` 默认是相对地址（自建服务）；设成别的 host 就模拟官方云端返回的
预签名地址。
"""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

FAKE_VERSION = "4.0.12"

# 慢速上传、下载的分块大小（X18 的取消测试：客户端在块之间检查 cancel_event）
_SLOW_CHUNK_BYTES = 64 * 1024

MIDDLE_JSON = {
    "schema": "docvortex.middle",
    "schema_version": "2.0",
    "metadata": {"file_suffix": "pdf", "producer": {"name": "mineru", "version": FAKE_VERSION}},
    "extensions": {
        "mineru": {"tier": "basic", "parse_mode": "txt"},
        "docvortex_layout": {
            "version": 1,
            "pages": [{"page_idx": 0, "width_pt": 600.0, "height_pt": 800.0}],
        },
    },
    "pages": [
        {
            "page_idx": 0,
            "blocks": [
                {
                    "type": "paragraph_title",
                    "index": 0,
                    "bbox": [0.1, 0.1, 0.5, 0.2],
                    "level": 2,
                    "content": [{"type": "text", "content": "假 MinerU 标题"}],
                },
                {
                    "type": "text",
                    "index": 1,
                    "bbox": [0.1, 0.3, 0.9, 0.4],
                    "content": [{"type": "text", "content": "假 MinerU 正文。"}],
                },
                {
                    "type": "image",
                    "index": 2,
                    "bbox": [0.1, 0.5, 0.5, 0.7],
                    "content": [
                        {
                            "type": "image_body",
                            "index": 2,
                            "image_path": "images/page_0_image_2.png",
                            "content": "",
                        }
                    ],
                },
            ],
        }
    ],
    "is_full_document": True,
}

MARKDOWN = "# 假 MinerU 标题\n\n假 MinerU 正文。\n"


def _tiny_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


TINY_PNG = _tiny_png()


def make_zip(
    middle_json: dict | None = None,
    images: dict[str, bytes] | None = None,
    markdown: str = MARKDOWN,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "middle_json.json", json.dumps(middle_json or MIDDLE_JSON, ensure_ascii=False)
        )
        archive.writestr("markdown.md", markdown)
        for name, data in (
            images if images is not None else {"images/page_0_image_2.png": TINY_PNG}
        ).items():
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeMineru:
    """一个假的 MinerU 服务，用 ``with FakeMineru() as server:`` 启动。"""

    def __init__(
        self,
        *,
        zip_bytes: bytes | None = None,
        upload_host: str | None = None,
        upload_read_delay_s: float = 0.0,
        download_chunk_delay_s: float = 0.0,
    ) -> None:
        self.zip_bytes = zip_bytes if zip_bytes is not None else make_zip()
        self.upload_host = upload_host
        # 慢速上传、下载：模拟大文件传输，用来验证取消后能尽快释放远程并发名额（X18）
        self.upload_read_delay_s = upload_read_delay_s
        self.download_chunk_delay_s = download_chunk_delay_s
        self.requests: list[tuple[str, str]] = []
        self.headers: list[tuple[str, dict[str, str]]] = []
        self.tiers: list[str] = []
        self.upload_bodies: list[bytes] = []
        self.uploads_started = 0
        self.downloads_started = 0
        self.job_polls = 0
        self.job_status = "completed"
        self.fail_message = "fake mineru failure"
        self.job_not_found = False
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> FakeMineru:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)

    # ---- 路由 ----
    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # 测试输出里不要访问日志
                pass

            def _record(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                body = self._read_body(length)
                fake.requests.append((self.command, self.path))
                fake.headers.append((self.path, dict(self.headers.items())))
                return body

            def _read_body(self, length: int) -> bytes:
                if not length:
                    return b""
                if not fake.upload_read_delay_s:
                    return self.rfile.read(length)
                # 慢速读取上传正文：客户端按块上传时会在块之间发现作业已取消（X18）
                parts = []
                remaining = length
                while remaining > 0:
                    part = self.rfile.read(min(_SLOW_CHUNK_BYTES, remaining))
                    if not part:
                        break
                    parts.append(part)
                    remaining -= len(part)
                    time.sleep(fake.upload_read_delay_s)
                return b"".join(parts)

            def _send(self, status: int, payload: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _send_zip(self) -> None:
                """返回 ZIP；配了下载延迟就分块慢写（X18）。"""
                payload = fake.zip_bytes
                delay = fake.download_chunk_delay_s
                if not delay:
                    self._send(200, payload, "application/zip")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                for start in range(0, len(payload), _SLOW_CHUNK_BYTES):
                    self.wfile.write(payload[start : start + _SLOW_CHUNK_BYTES])
                    self.wfile.flush()
                    # 每块都重新读一遍延迟，测试里改成 0 后剩下的块立刻发完
                    time.sleep(fake.download_chunk_delay_s)

            def _json(self, status: int, data: dict) -> None:
                self._send(status, json.dumps(data).encode(), "application/json")

            def _route(self, body: bytes) -> None:
                path = self.path.split("?", 1)[0]
                if self.command == "GET" and path == "/v1/health":
                    self._json(200, {"status": "ok", "version": FAKE_VERSION})
                elif self.command == "POST" and path == "/v1/uploads":
                    fake.upload_bodies.append(body)
                    upload_url = "/v1/uploads/up1/content"
                    if fake.upload_host:
                        upload_url = f"{fake.upload_host}/v1/uploads/up1/content"
                    self._json(
                        200,
                        {
                            "id": "up1",
                            "object": "upload",
                            "status": "pending",
                            "upload_url": upload_url,
                            "upload_method": "PUT",
                            "upload_headers": {"x-fake-blob-type": "BlockBlob"},
                        },
                    )
                elif self.command == "PUT" and path == "/v1/uploads/up1/content":
                    self._send(200, b"", "application/octet-stream")
                elif self.command == "POST" and path == "/v1/uploads/up1/complete":
                    self._json(200, {"id": "up1", "status": "completed", "file": {"id": "f1"}})
                elif self.command == "POST" and path == "/v1/parse/jobs":
                    payload = json.loads(body or b"{}")
                    fake.tiers.append(str(payload.get("tier")))
                    self._json(200, {"job_id": "j1", "status": "queued"})
                elif self.command == "GET" and path == "/v1/parse/jobs/j1":
                    if fake.job_not_found:
                        self._json(
                            404, {"error": {"code": "not_found", "message": "job not found"}}
                        )
                        return
                    fake.job_polls += 1
                    if fake.job_polls < 2:
                        # 第一次轮询还是排队中，客户端要真的轮询到终态
                        self._json(
                            200,
                            {
                                "job_id": "j1",
                                "status": "queued",
                                "progress": {"completed": 0, "failed": 0, "total": 1},
                                "files": [],
                            },
                        )
                        return
                    self._json(200, _terminal_job(fake))
                elif self.command == "GET" and path == "/v1/files/zip1/content":
                    fake.downloads_started += 1
                    self._send_zip()
                else:  # pragma: no cover - 测试里出现未覆盖的路径应当直接失败
                    self._json(404, {"error": {"code": "not_found", "message": f"no route {path}"}})

            def do_GET(self) -> None:
                self._route(self._record())

            def do_POST(self) -> None:
                self._route(self._record())

            def do_PUT(self) -> None:
                if self.path.startswith("/v1/uploads/"):
                    fake.uploads_started += 1
                self._route(self._record())

        return Handler


def _terminal_job(fake: FakeMineru) -> dict:
    if fake.job_status in ("failed", "canceled"):
        files = [
            {
                "name": "a.pdf",
                "status": fake.job_status,
                "error": {"code": "parse_error", "message": fake.fail_message},
            }
        ]
    else:
        files = [
            {
                "name": "a.pdf",
                "status": "completed",
                "output_files": {"zip": {"file_id": "zip1", "bytes": len(fake.zip_bytes)}},
            }
        ]
    return {
        "job_id": "j1",
        "status": fake.job_status,
        "progress": {"completed": 1, "failed": 0, "total": 1},
        "files": files,
    }
