"""进程池的超时、取消与子进程补齐。"""

from __future__ import annotations

import time

from tests.conftest import AUTH, submit
from tests.test_api import poll


def wait_alive(client, expected, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.app.state.runner.alive_workers() == expected:
            return
        time.sleep(0.1)
    raise AssertionError(f"子进程数未恢复到 {expected}")


def test_timeout_kills_worker_and_replaces(make_client, s3):
    client = make_client(parser_workers=1, parser_job_timeout_s=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"x")

    job_id = submit(client, engine_params={"pages": 1, "sleep_s": 5}).json()["id"]
    status = poll(client, job_id, {"failed", "succeeded"}, timeout=20)
    assert status["status"] == "failed", status
    assert status["error"]["code"] == "TIMEOUT"

    # 子进程被补齐，数量仍为 PARSER_WORKERS
    wait_alive(client, 1)

    # 新作业能正常执行
    ok_id = submit(client, engine_params={"pages": 1}).json()["id"]
    ok = poll(client, ok_id, {"succeeded", "failed"}, timeout=20)
    assert ok["status"] == "succeeded", ok


def test_cancel_running_kills_worker_and_replaces(make_client, s3):
    client = make_client(parser_workers=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"x")

    job_id = submit(client, engine_params={"pages": 1, "sleep_s": 5}).json()["id"]
    poll(client, job_id, {"running"}, timeout=10)

    response = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["error"]["code"] == "CANCELLED"

    wait_alive(client, 1)

    ok_id = submit(client, engine_params={"pages": 1}).json()["id"]
    ok = poll(client, ok_id, {"succeeded", "failed"}, timeout=20)
    assert ok["status"] == "succeeded", ok


def test_cancel_queued_job(make_client, s3):
    client = make_client(parser_workers=1)
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"x")

    first = submit(client, engine_params={"pages": 1, "sleep_s": 5}).json()["id"]
    poll(client, first, {"running"}, timeout=10)
    second = submit(client, engine_params={"pages": 1}).json()["id"]
    assert client.get(f"/v1/jobs/{second}", headers=AUTH).json()["status"] == "queued"

    response = client.delete(f"/v1/jobs/{second}", headers=AUTH)
    assert response.json()["status"] == "cancelled"
