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
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

FAKE_VERSION = "4.0.12"

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


def make_zip(middle_json: dict | None = None, images: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "middle_json.json", json.dumps(middle_json or MIDDLE_JSON, ensure_ascii=False)
        )
        archive.writestr("markdown.md", MARKDOWN)
        for name, data in (
            images if images is not None else {"images/page_0_image_2.png": TINY_PNG}
        ).items():
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeMineru:
    """一个假的 MinerU 服务，用 ``with FakeMineru() as server:`` 启动。"""

    def __init__(self, *, zip_bytes: bytes | None = None, upload_host: str | None = None) -> None:
        self.zip_bytes = zip_bytes if zip_bytes is not None else make_zip()
        self.upload_host = upload_host
        self.requests: list[tuple[str, str]] = []
        self.headers: list[tuple[str, dict[str, str]]] = []
        self.tiers: list[str] = []
        self.upload_bodies: list[bytes] = []
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
                body = self.rfile.read(length) if length else b""
                fake.requests.append((self.command, self.path))
                fake.headers.append((self.path, dict(self.headers.items())))
                return body

            def _send(self, status: int, payload: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

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
                    self._send(200, fake.zip_bytes, "application/zip")
                else:  # pragma: no cover - 测试里出现未覆盖的路径应当直接失败
                    self._json(404, {"error": {"code": "not_found", "message": f"no route {path}"}})

            def do_GET(self) -> None:
                self._route(self._record())

            def do_POST(self) -> None:
                self._route(self._record())

            def do_PUT(self) -> None:
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
