"""走 ``POST /v1/jobs`` 的 MinerU 端到端测试：假 MinerU 服务 + moto S3 + 真子进程。

解析子进程和主进程共用环境变量，所以 ``MINERU_URL`` 指向假服务即可跑通整条链路：
提交作业 → 下载源文件 → 调 MinerU V1 接口 → 转换 → 上传 doc.json/doc.md/图片。
"""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

from PIL import Image

from app.engines.mineru.client import MAX_FILE_MIB
from tests.conftest import AUTH, submit
from tests.engines.mineru.fake_server import FakeMineru, make_zip

PDF_BYTES = (Path(__file__).parent / "samples/sample.pdf").read_bytes()

# X18 的取消测试：上传、下载都慢到 20 秒以上，取消后名额必须提前释放
SLOW_DELAY_S = 0.25
BIG_BYTES = 6 * 1024 * 1024


def _big_png() -> bytes:
    """接近 6MiB 的 PNG（随机像素压不动），用来模拟大文件上传。"""
    image = Image.frombytes("RGB", (1400, 1400), os.urandom(1400 * 1400 * 3))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


BIG_PNG_BYTES = _big_png()


def poll(client, job_id, statuses, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in statuses:
            return last
        time.sleep(0.05)
    raise AssertionError(f"等待 {statuses} 超时，最后状态：{last}")


def test_mineru_job_end_to_end(make_client, s3, monkeypatch):
    with FakeMineru() as server:
        monkeypatch.setenv("MINERU_URL", server.url)
        monkeypatch.setenv("MINERU_API_KEY", "key")
        client = make_client(parser_workers=1)
        s3.put_object(Bucket="test-bucket", Key="in/a.pdf", Body=PDF_BYTES)

        response = submit(
            client,
            source_key="in/a.pdf",
            file_type="pdf",
            ext="pdf",
            engine="mineru",
            engine_params={"tier": "basic"},
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]

        status = poll(client, job_id, {"succeeded", "failed"})
        assert status["status"] == "succeeded", status
        result = status["result"]
        assert result["engine"] == "mineru"
        assert result["engine_version"] == "mineru-4.0.12"
        assert result["pages"] == 1
        assert status["progress"] == {"done_pages": 1, "total_pages": 1}
        assert server.tiers == ["basic"]

        # doc.json 是转换后的 DoclingDocument，带页码和坐标
        payload = json.loads(
            s3.get_object(Bucket="test-bucket", Key=result["doc_key"])["Body"].read()
        )
        assert [text["text"] for text in payload["texts"]] == ["假 MinerU 标题", "假 MinerU 正文。"]
        assert payload["texts"][0]["prov"][0]["page_no"] == 1
        assert payload["pages"]["1"]["size"]["width"] == 600.0

        # doc.md 是 Markdown 导出
        markdown = s3.get_object(Bucket="test-bucket", Key=result["md_key"])["Body"].read()
        assert "假 MinerU" in markdown.decode("utf-8")

        # 图片由 X03 统一存成 PNG 并上传到 images_prefix
        image = s3.get_object(Bucket="test-bucket", Key="out/images/1.png")["Body"].read()
        assert image.startswith(b"\x89PNG")


def test_mineru_standard_without_remote_fails(make_client, s3, monkeypatch):
    """standard 没有 remote 时作业失败为 ENGINE_UNAVAILABLE。"""
    with FakeMineru() as server:
        monkeypatch.setenv("MINERU_URL", server.url)
        client = make_client(parser_workers=1)
        s3.put_object(Bucket="test-bucket", Key="in/a.pdf", Body=PDF_BYTES)

        response = submit(
            client,
            source_key="in/a.pdf",
            file_type="pdf",
            ext="pdf",
            engine="mineru",
            engine_params={"tier": "standard"},
        )
        assert response.status_code == 202, response.text
        status = poll(client, response.json()["id"], {"succeeded", "failed"})

    assert status["status"] == "failed"
    assert status["error"]["code"] == "ENGINE_UNAVAILABLE"
    assert "标准" in status["error"]["message"]
    assert server.tiers == []


def test_mineru_remote_options_are_passed_through(make_client, s3, monkeypatch):
    """标准档用作业请求里的 remote 地址（主服务从系统设置取出后传入）。"""
    with FakeMineru() as local, FakeMineru() as remote:
        monkeypatch.setenv("MINERU_URL", local.url)
        client = make_client(parser_workers=1)
        s3.put_object(Bucket="test-bucket", Key="in/a.pdf", Body=PDF_BYTES)

        response = submit(
            client,
            source_key="in/a.pdf",
            file_type="pdf",
            ext="pdf",
            engine="mineru",
            engine_params={"tier": "advanced"},
            remote={"base_url": remote.url, "api_key": "cloud-key"},
        )
        status = poll(client, response.json()["id"], {"succeeded", "failed"})

    assert status["status"] == "succeeded", status
    assert remote.tiers == ["advanced"]
    # 本地服务只被健康检查碰过，解析请求全走了 remote
    assert [path for _, path in local.requests] == ["/v1/health"]


def test_mineru_job_fails_when_service_missing(make_client, s3, monkeypatch):
    """MINERU_URL 没配时引擎不可用，提交作业返回 400。"""
    monkeypatch.delenv("MINERU_URL", raising=False)
    client = make_client(parser_workers=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.pdf", Body=PDF_BYTES)

    response = submit(
        client,
        source_key="in/a.pdf",
        file_type="pdf",
        ext="pdf",
        engine="mineru",
        engine_params={"tier": "basic"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "ENGINE_UNAVAILABLE"
    assert response.json()["message"] == "未部署"


def test_engines_endpoint_reports_mineru(make_client, monkeypatch):
    with FakeMineru() as server:
        monkeypatch.setenv("MINERU_URL", server.url)
        client = make_client(parser_workers=1)
        engines = {item["name"]: item for item in client.get("/v1/engines", headers=AUTH).json()}

    mineru = engines["mineru"]
    assert mineru["available"] is True
    assert "pdf" in mineru["file_types"]
    assert "epub" in mineru["file_types"]
    assert "excel" not in mineru["file_types"]
    assert MAX_FILE_MIB == 200


def _mineru_submit(client, source_key="in/a.pdf", file_type="pdf", ext="pdf"):
    response = submit(client, source_key=source_key, file_type=file_type, ext=ext, engine="mineru")
    assert response.status_code == 202, response.text
    return response.json()["id"]


def _wait_started(server, attr, timeout=30):
    """等到假服务真的开始收上传 / 发下载（X18）。"""
    deadline = time.monotonic() + timeout
    while getattr(server, attr) < 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert getattr(server, attr) >= 1


def _wait_for_polls(server, count=1):
    deadline = time.monotonic() + 30
    while server.job_polls < count and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.job_polls >= count


def test_remote_wait_does_not_block_local_worker(make_client, s3, monkeypatch):
    with FakeMineru() as server:
        server.job_status = "running"
        monkeypatch.setenv("MINERU_URL", server.url)
        client = make_client(parser_workers=1)
        s3.put_object(Bucket="test-bucket", Key="in/a.pdf", Body=PDF_BYTES)
        s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"local text")
        remote = _mineru_submit(client)
        _wait_for_polls(server)
        local = submit(client, engine="builtin").json()["id"]
        assert poll(client, local, {"succeeded", "failed"})["status"] == "succeeded"
        assert client.get(f"/v1/jobs/{remote}", headers=AUTH).json()["status"] == "running"
        client.delete(f"/v1/jobs/{remote}", headers=AUTH)


def test_remote_cancel_timeout_and_queue(make_client, s3, monkeypatch):
    with FakeMineru() as server:
        server.job_status = "running"
        monkeypatch.setenv("MINERU_URL", server.url)
        client = make_client(parser_workers=1, parser_remote_concurrency=1, parser_job_timeout_s=3)
        s3.put_object(Bucket="test-bucket", Key="in/a.pdf", Body=PDF_BYTES)
        first = _mineru_submit(client)
        _wait_for_polls(server)
        second = _mineru_submit(client)
        time.sleep(0.4)
        assert client.get(f"/v1/jobs/{second}", headers=AUTH).json()["status"] == "queued"
        assert len(server.tiers) == 1
        assert (
            client.delete(f"/v1/jobs/{first}", headers=AUTH).json()["error"]["code"] == "CANCELLED"
        )
        _wait_for_polls(server, 2)
        assert poll(client, second, {"failed"})["error"]["code"] == "TIMEOUT"
        time.sleep(0.3)
        count = server.job_polls
        time.sleep(1.3)
        assert server.job_polls == count
        server.job_status = "completed"
        third = _mineru_submit(client)
        assert poll(client, third, {"succeeded", "failed"})["status"] == "succeeded"


def test_remote_cancel_during_upload_releases_slot(make_client, s3, monkeypatch):
    """上传途中取消：远程并发名额立刻释放，排队的远程作业能开始（X18）。"""
    with FakeMineru(upload_read_delay_s=SLOW_DELAY_S) as server:
        monkeypatch.setenv("MINERU_URL", server.url)
        client = make_client(parser_workers=1, parser_remote_concurrency=1)
        s3.put_object(Bucket="test-bucket", Key="in/big.png", Body=BIG_PNG_BYTES)
        s3.put_object(Bucket="test-bucket", Key="in/a.pdf", Body=PDF_BYTES)

        first = _mineru_submit(client, source_key="in/big.png", file_type="image", ext="png")
        _wait_started(server, "uploads_started")
        second = _mineru_submit(client)
        time.sleep(0.3)
        assert client.get(f"/v1/jobs/{second}", headers=AUTH).json()["status"] == "queued"

        cancelled = client.delete(f"/v1/jobs/{first}", headers=AUTH)
        assert cancelled.json()["error"]["code"] == "CANCELLED"
        # 6MiB 按 64KiB、每块 0.25 秒要传 20 秒以上；名额没提前释放的话这里会超时
        assert poll(client, second, {"succeeded", "failed"}, timeout=15)["status"] == "succeeded"


def test_remote_cancel_during_download_releases_slot(make_client, s3, monkeypatch):
    """下载 ZIP 途中取消：远程并发名额立刻释放，排队的远程作业能开始（X18）。"""
    zip_bytes = make_zip(markdown="x" * BIG_BYTES)
    with FakeMineru(zip_bytes=zip_bytes, download_chunk_delay_s=SLOW_DELAY_S) as server:
        monkeypatch.setenv("MINERU_URL", server.url)
        client = make_client(parser_workers=1, parser_remote_concurrency=1)
        s3.put_object(Bucket="test-bucket", Key="in/a.pdf", Body=PDF_BYTES)

        first = _mineru_submit(client)
        _wait_started(server, "downloads_started")
        second = _mineru_submit(client)
        time.sleep(0.3)
        assert client.get(f"/v1/jobs/{second}", headers=AUTH).json()["status"] == "queued"

        cancelled = client.delete(f"/v1/jobs/{first}", headers=AUTH)
        assert cancelled.json()["error"]["code"] == "CANCELLED"
        # 6MiB 按 64KiB、每块 0.25 秒要下 20 秒以上；名额没提前释放的话第二个作业会一直排队，
        # 这里就会超时失败
        poll(client, second, {"running", "succeeded"}, timeout=15)
        # 后面的下载不再放慢，让两个作业都能收尾
        server.download_chunk_delay_s = 0.0
        assert poll(client, second, {"succeeded", "failed"}, timeout=15)["status"] == "succeeded"
