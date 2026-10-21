from __future__ import annotations

import base64
from contextlib import ExitStack

import app as app_module
import pytest
from adp_parser.config import Limits, Settings
from fastapi.testclient import TestClient


@pytest.fixture
def make_client():
    with ExitStack() as stack:

        def create(settings: Settings | None = None) -> TestClient:
            effective = settings or Settings(token=None, timeout_seconds=15)
            return stack.enter_context(TestClient(app_module.create_app(effective)))

        yield create


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def small_limits_client(make_client):
    return make_client(
        Settings(
            token=None,
            timeout_seconds=15,
            limits=Limits(max_input_bytes=1024 * 1024),
        )
    )


@pytest.fixture
def parse_text():
    def _parse(client: TestClient, file_name: str, text: str, **extra):
        body = {"fileName": file_name, "text": text}
        body.update(extra)
        return client.post("/v1/parse", json=body)

    return _parse


@pytest.fixture
def parse_bytes():
    def _parse(client: TestClient, file_name: str, data: bytes, **extra):
        body = {
            "fileName": file_name,
            "dataBase64": base64.b64encode(data).decode("ascii"),
        }
        body.update(extra)
        return client.post("/v1/parse", json=body)

    return _parse
