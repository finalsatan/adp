"""走 ``POST /v1/jobs`` 的 MinerU 端到端测试：假 MinerU 服务 + moto S3 + 真子进程。

解析子进程和主进程共用环境变量，所以 ``MINERU_URL`` 指向假服务即可跑通整条链路：
提交作业 → 下载源文件 → 调 MinerU V1 接口 → 转换 → 上传 doc.json/doc.md/图片。
"""

from __future__ import annotations

import json
import time

from app.engines.mineru.client import MAX_FILE_MIB
from tests.conftest import AUTH, submit
from tests.engines.mineru.fake_server import FakeMineru

PDF_BYTES = b"%PDF-1.4 fake pdf"


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
