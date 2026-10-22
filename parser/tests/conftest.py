"""测试公共夹具：moto 模拟 S3，TestClient 启动完整应用。"""

from __future__ import annotations

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.config import Settings
from app.main import create_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_settings(**overrides) -> Settings:
    base = dict(
        parser_token=TOKEN,
        parser_workers=1,
        parser_queue_max=50,
        parser_job_timeout_s=30,
        parser_max_pages=2000,
        # 单元测试用 moto 模拟 S3，不设自定义 endpoint，否则 moto 拦截不到
        s3_endpoint="",
        s3_bucket="test-bucket",
        s3_access_key="testing",
        s3_secret_key="testing",
        parser_enable_fake_engine=True,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def s3(aws_env):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bucket")
        yield client


@pytest.fixture
def make_client(s3):
    """返回一个工厂，创建并进入 TestClient（触发 lifespan 启动进程池）。"""
    stack: list[TestClient] = []

    def _make(**overrides) -> TestClient:
        client = TestClient(create_app(make_settings(**overrides)))
        client.__enter__()
        stack.append(client)
        return client

    yield _make
    for client in reversed(stack):
        client.__exit__(None, None, None)


def submit(client: TestClient, **overrides):
    payload = {
        "source_key": "in/a.txt",
        "file_type": "txt",
        "ext": "txt",
        "engine": "fake",
        "engine_params": {},
        "output_prefix": "out/parsed/original",
        "images_prefix": "out/images",
    }
    payload.update(overrides)
    return client.post("/v1/jobs", json=payload, headers=AUTH)
