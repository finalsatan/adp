"""作业接口的行为测试（假引擎 + moto）。"""

from __future__ import annotations

import time

from docling_core.types.doc.document import DoclingDocument

from app.engines import registry
from tests.conftest import AUTH, submit


def poll(client, job_id, statuses, timeout=30):
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


def test_full_flow(make_client, s3):
    client = make_client(parser_workers=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body="你好".encode())

    response = submit(client, engine_params={"pages": 3, "sleep_s": 0.05})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]

    status = poll(client, job_id, {"succeeded", "failed"})
    assert status["status"] == "succeeded", status
    result = status["result"]
    assert result["pages"] == 3
    assert result["engine"] == "fake"
    assert result["engine_version"] == "fake-0.1"
    assert result["doc_key"] == "out/parsed/original/doc.json"
    assert result["md_key"] == "out/parsed/original/doc.md"
    assert status["progress"] == {"done_pages": 3, "total_pages": 3}

    doc_bytes = s3.get_object(Bucket="test-bucket", Key=result["doc_key"])["Body"].read()
    md_bytes = s3.get_object(Bucket="test-bucket", Key=result["md_key"])["Body"].read()
    assert doc_bytes
    assert md_bytes

    # doc.json 能被 DoclingDocument 加载
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".json") as handle:
        handle.write(doc_bytes)
        handle.flush()
        document = DoclingDocument.load_from_json(handle.name)
    assert len(document.texts) == 2


def test_progress_grows_to_total(make_client, s3):
    client = make_client(parser_workers=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"x")
    job_id = submit(client, engine_params={"pages": 3, "sleep_s": 0.3}).json()["id"]

    seen: set[int] = set()
    deadline = time.time() + 30
    while time.time() < deadline:
        data = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        seen.add(data["progress"]["done_pages"])
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.05)

    assert 3 in seen
    assert any(0 < done < 3 for done in seen), seen


def test_engine_error_returns_code_and_chinese_message(make_client, s3):
    client = make_client(parser_workers=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"x")
    job_id = submit(client, engine_params={"pages": 1, "error_code": "FILE_CORRUPTED"}).json()["id"]

    status = poll(client, job_id, {"failed"})
    assert status["error"]["code"] == "FILE_CORRUPTED"
    message = status["error"]["message"]
    assert message
    assert any("\u4e00" <= char <= "\u9fff" for char in message), message


def test_too_many_pages(make_client, s3):
    client = make_client(parser_workers=1, parser_max_pages=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"x")
    job_id = submit(client, engine_params={"pages": 2}).json()["id"]

    status = poll(client, job_id, {"failed"})
    assert status["error"]["code"] == "TOO_MANY_PAGES"


def test_unknown_file_type(make_client, s3):
    client = make_client()
    response = submit(client, file_type="unknown", ext="xyz")
    assert response.status_code == 400
    assert response.json()["code"] == "UNSUPPORTED_TYPE"


def test_unknown_engine(make_client, s3):
    client = make_client()
    response = submit(client, engine="nope")
    assert response.status_code == 400
    assert response.json()["code"] == "ENGINE_UNAVAILABLE"


def test_engine_does_not_support_type(make_client, s3):
    class OnlyTxt:
        name = "onlytxt"

        def supports(self, file_type, ext):
            return file_type == "txt"

        def availability(self):
            return True, None

        def parse(self, ctx):
            raise NotImplementedError

    client = make_client()
    registry.register(OnlyTxt())
    response = submit(client, engine="onlytxt", file_type="pdf", ext="pdf")
    assert response.status_code == 400
    assert response.json()["code"] == "UNSUPPORTED_TYPE"


def test_engine_unavailable(make_client, s3):
    class Unavailable:
        name = "unavailable"

        def supports(self, file_type, ext):
            return True

        def availability(self):
            return False, "引擎未部署"

        def parse(self, ctx):
            raise NotImplementedError

    client = make_client()
    registry.register(Unavailable())
    response = submit(client, engine="unavailable")
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "ENGINE_UNAVAILABLE"
    assert body["message"] == "引擎未部署"


def test_auth(make_client):
    client = make_client()
    assert client.get("/healthz").status_code == 200
    assert client.get("/v1/engines").status_code == 401
    assert client.get("/v1/engines", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/engines", headers=AUTH).status_code == 200
    assert client.post("/v1/jobs", json={}).status_code == 401


def test_job_not_found(make_client):
    client = make_client()
    assert client.get("/v1/jobs/does-not-exist", headers=AUTH).status_code == 404
    assert client.delete("/v1/jobs/does-not-exist", headers=AUTH).status_code == 404


def test_queue_full_returns_429(make_client, s3):
    client = make_client(parser_workers=1, parser_queue_max=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"x")

    first = submit(client, engine_params={"pages": 1, "sleep_s": 5}).json()["id"]
    # 等第一个作业真正进入 running，避免调度延迟影响排队计数
    poll(client, first, {"running"}, timeout=10)

    second = submit(client, engine_params={"pages": 1, "sleep_s": 5})
    assert second.status_code == 202, second.text
    second_id = second.json()["id"]
    assert client.get(f"/v1/jobs/{second_id}", headers=AUTH).json()["status"] == "queued"

    third = submit(client, engine_params={"pages": 1, "sleep_s": 5})
    assert third.status_code == 429, third.text
    assert third.json()["code"] == "QUEUE_FULL"


def test_engines_list(make_client):
    client = make_client()
    engines = client.get("/v1/engines", headers=AUTH).json()
    names = {engine["name"]: engine for engine in engines}
    assert "fake" in names
    assert names["fake"]["available"] is True
    assert "txt" in names["fake"]["file_types"]


def test_docling_document_roundtrip_in_result(make_client, s3):
    """ParseOutput 类型跨进程传回后仍是 DoclingDocument。"""
    client = make_client(parser_workers=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"x")
    job_id = submit(client, engine_params={"pages": 1}).json()["id"]
    status = poll(client, job_id, {"succeeded", "failed"})
    assert status["status"] == "succeeded"
    doc_bytes = s3.get_object(Bucket="test-bucket", Key=status["result"]["doc_key"])["Body"].read()
    assert doc_bytes
