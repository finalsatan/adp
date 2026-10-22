"""对象存储读写（S3 兼容，RustFS）。"""

from __future__ import annotations

from pathlib import Path

import boto3
from botocore.config import Config

from .config import Settings


class Storage:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    def download(self, key: str, dest: str | Path) -> None:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        with open(dest, "wb") as handle:
            for chunk in body.iter_chunks():
                handle.write(chunk)

    def upload_file(self, local_path: str | Path, key: str) -> None:
        self._client.upload_file(str(local_path), self._bucket, key)

    def put_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)

    def get_bytes(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
