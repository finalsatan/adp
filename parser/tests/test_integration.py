"""连本地 RustFS 的冒烟测试，默认不跑（需要 make parser-dev-up）。"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

import pytest

from app.config import Settings
from app.storage import Storage

pytestmark = pytest.mark.integration

ENDPOINT = "http://localhost:59000"
BUCKET = "adp-parser"


def _rustfs_available() -> bool:
    parsed = urlparse(ENDPOINT)
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def storage() -> Storage:
    if not _rustfs_available():
        pytest.skip("RustFS 未启动，先执行 make parser-dev-up")
    settings = Settings(
        s3_endpoint=ENDPOINT,
        s3_bucket=BUCKET,
        s3_access_key="adpdev",
        s3_secret_key="adpdevsecret",
    )
    return Storage(settings)


def test_rustfs_roundtrip(storage):
    key = "tmp/integration-smoke.txt"
    storage.put_bytes(key, b"rustfs-smoke")
    assert storage.get_bytes(key) == b"rustfs-smoke"
