"""MinerU V1 客户端的 HTTP 模拟测试（httpx.MockTransport）。

覆盖知识库技术方案 19.3 要求的两条：官方云端上传不带 MinerU 密钥、自建服务上传
带密钥；以及作业查询 404（mineru 重启导致作业丢失）要失败成 ``REMOTE_ERROR``。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import httpx
import pytest

from app.engines.mineru import client as client_module
from app.engines.mineru.client import MAX_FILE_BYTES, MineruClient, read_artifacts
from app.errors import ParseError

API = "https://api.mineru.example.com"


def make_client(handler, *, api_key="secret", **kwargs) -> MineruClient:
    return MineruClient(
        API, api_key, transport=httpx.MockTransport(handler), poll_interval=0, **kwargs
    )


def upload_flow(seen: dict):
    """返回一个处理上传会话三个请求的 handler。"""

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.method + " " + str(request.url)] = request
        if request.url.path == "/v1/uploads":
            return httpx.Response(
                200,
                json={
                    "id": "up1",
                    "status": "pending",
                    "upload_url": "/v1/uploads/up1/content",
                    "upload_method": "PUT",
                    "upload_headers": {"x-blob": "1"},
                },
            )
        if request.url.path == "/v1/uploads/up1/complete":
            return httpx.Response(
                200, json={"id": "up1", "status": "completed", "file": {"id": "f1"}}
            )
        return httpx.Response(200, text="")

    return handler


def test_upload_sends_key_to_same_origin(tmp_path):
    """自建服务返回同源上传地址，要带 MinerU 密钥。"""
    source = tmp_path / "a.pdf"
    source.write_bytes(b"%PDF-1.4 hello")
    seen: dict = {}
    client = make_client(upload_flow(seen))
    assert client.upload(source) == "f1"
    put = seen[f"PUT {API}/v1/uploads/up1/content"]
    assert put.headers["authorization"] == "Bearer secret"
    assert put.headers["x-blob"] == "1"
    assert put.content == b"%PDF-1.4 hello"


def test_upload_skips_key_for_presigned_url(tmp_path):
    """官方云端返回不同源的预签名地址，绝不能带 MinerU 密钥。"""
    source = tmp_path / "a.pdf"
    source.write_bytes(b"%PDF-1.4 hello")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.method + " " + str(request.url)] = request
        if request.url.path == "/v1/uploads":
            return httpx.Response(
                200,
                json={
                    "id": "up1",
                    "status": "pending",
                    "upload_url": "https://storage.example.com/presigned/up1?signature=abc",
                    "upload_method": "PUT",
                    "upload_headers": {"x-ms-blob-type": "BlockBlob"},
                },
            )
        if request.url.path == "/v1/uploads/up1/complete":
            return httpx.Response(200, json={"status": "completed", "file": {"id": "f1"}})
        return httpx.Response(200, text="")

    client = make_client(handler)
    assert client.upload(source) == "f1"
    put = seen["PUT https://storage.example.com/presigned/up1?signature=abc"]
    assert "authorization" not in put.headers
    assert put.headers["x-ms-blob-type"] == "BlockBlob"


def test_upload_deduplicated_file(tmp_path):
    """命中已有文件时服务直接返回 completed 和内嵌 file，跳过上传和完成两步。"""
    source = tmp_path / "a.pdf"
    source.write_bytes(b"%PDF-1.4 hello")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"id": "up1", "status": "completed", "file": {"id": "f9"}})

    client = make_client(handler)
    assert client.upload(source) == "f9"
    assert calls == ["/v1/uploads"]


def test_upload_rejects_file_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(client_module, "MAX_FILE_BYTES", 10)
    source = tmp_path / "a.pdf"
    source.write_bytes(b"x" * 11)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应发出请求
        raise AssertionError("超限文件不应上传")

    with pytest.raises(ParseError) as excinfo:
        make_client(handler).upload(source)
    assert excinfo.value.code == "ARCHIVE_TOO_LARGE"
    assert "200 MiB" in excinfo.value.message
    assert MAX_FILE_BYTES == 200 * 1024 * 1024


def test_submit_requests_zip_output(tmp_path):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"job_id": "j1", "status": "queued"})

    assert make_client(handler).submit("f1", "basic") == "j1"
    assert bodies == [
        {
            "files": [{"source": {"type": "file_id", "file_id": "f1"}}],
            "tier": "basic",
            "output_formats": ["zip"],
        }
    ]


def test_job_not_found_is_remote_error():
    """mineru 重启后作业丢失：查询返回 404，作业失败成 REMOTE_ERROR。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"error": {"code": "not_found", "message": "job not found"}}
        )

    with pytest.raises(ParseError) as excinfo:
        make_client(handler).wait("j1")
    assert excinfo.value.code == "REMOTE_ERROR"
    assert "重启" in excinfo.value.message
    assert "请重试" in excinfo.value.message


def test_failed_job_reports_mineru_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "job_id": "j1",
                "status": "failed",
                "files": [
                    {
                        "name": "a.pdf",
                        "status": "failed",
                        "error": {"code": "x", "message": "broken pdf"},
                    }
                ],
            },
        )

    with pytest.raises(ParseError) as excinfo:
        make_client(handler).wait("j1")
    assert excinfo.value.code == "REMOTE_ERROR"
    assert "broken pdf" in excinfo.value.message


def test_partial_job_is_remote_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job_id": "j1", "status": "partial", "files": []})

    with pytest.raises(ParseError) as excinfo:
        make_client(handler).wait("j1")
    assert excinfo.value.code == "REMOTE_ERROR"


def test_wait_polls_until_terminal_and_reports_progress():
    polls = {"n": 0}
    progress: list[tuple[str, int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        polls["n"] += 1
        status = "queued" if polls["n"] < 3 else "completed"
        return httpx.Response(
            200,
            json={
                "job_id": "j1",
                "status": status,
                "progress": {"completed": 0, "failed": 0, "total": 1},
                "files": [{"output_files": {"zip": {"file_id": "zip1"}}}],
            },
        )

    job = make_client(handler).wait("j1", on_progress=lambda s, d, t: progress.append((s, d, t)))
    assert polls["n"] == 3
    assert job["status"] == "completed"
    assert progress == [("queued", 0, 1), ("queued", 0, 1), ("completed", 0, 1)]


def test_health_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    assert make_client(handler).health() == (False, "连接失败")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert make_client(boom).health() == (False, "连接失败")


def test_download_zip_missing_output():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job_id": "j1", "status": "completed", "files": [{}]})

    with pytest.raises(ParseError) as excinfo:
        make_client(handler).download_zip({"files": [{}]})
    assert excinfo.value.code == "REMOTE_ERROR"
    assert "ZIP" in excinfo.value.message


def test_read_artifacts_reads_middle_json_markdown_and_images():
    middle = {
        "schema": "docvortex.middle",
        "pages": [],
        "extensions": {"mineru": {"tier": "basic"}},
    }
    payload = _zip(middle, "hello", {"images/a.jpg": b"jpeg-bytes"})
    artifacts = read_artifacts(payload)
    assert artifacts.middle_json == middle
    assert artifacts.markdown == "hello"
    assert artifacts.images == {"images/a.jpg": b"jpeg-bytes"}
    assert artifacts.tier == "basic"


def test_read_artifacts_rejects_broken_zip():
    with pytest.raises(ParseError) as excinfo:
        read_artifacts(b"not a zip")
    assert excinfo.value.code == "REMOTE_ERROR"

    with pytest.raises(ParseError):
        read_artifacts(_zip({}, "", {}))


def test_upload_sends_sha256(tmp_path):
    source = tmp_path / "a.pdf"
    source.write_bytes(b"%PDF-1.4 hello")
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "up1", "status": "completed", "file": {"id": "f1"}})

    make_client(handler).upload(source)
    assert bodies[0]["sha256sum"] == hashlib.sha256(b"%PDF-1.4 hello").hexdigest()
    assert bodies[0]["mime_type"] == "application/pdf"
    assert bodies[0]["bytes"] == len(b"%PDF-1.4 hello")
    assert bodies[0]["purpose"] == "parse"


def test_error_response_message_is_surfaced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": "internal", "message": "boom"}})

    with pytest.raises(ParseError) as excinfo:
        make_client(handler).submit("f1", "basic")
    assert "boom" in excinfo.value.message


def _zip(middle: dict, markdown: str, images: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if middle:
            archive.writestr("middle_json.json", json.dumps(middle))
        archive.writestr("markdown.md", markdown)
        for name, data in images.items():
            archive.writestr(name, data)
    return buffer.getvalue()
