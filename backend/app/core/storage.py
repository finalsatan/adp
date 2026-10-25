"""对象存储读写（S3 兼容，RustFS）：知识库技术方案 11.1、18。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO

import anyio
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.core.errors import StorageError

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}


def _is_not_found(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code", "") in _NOT_FOUND_CODES


def doc_file_key(kb_id: int, doc_id: int, name: str) -> str:
    """文档目录下的文件键，如 `original.pdf`、`converted.docx`、`preview.pdf`。"""
    return f"kb/{kb_id}/docs/{doc_id}/{name}"


def kb_original_key(kb_id: int, doc_id: int, ext: str) -> str:
    """原文件：`kb/{kb_id}/docs/{doc_id}/original.{ext}`。"""
    return doc_file_key(kb_id, doc_id, f"original.{ext}")


def kb_prefix(kb_id: int) -> str:
    """知识库前缀，删除知识库时按它整棵删除。"""
    return f"kb/{kb_id}/"


def doc_prefix(kb_id: int, doc_id: int) -> str:
    """文档前缀，删除文档时按它整棵删除。"""
    return f"kb/{kb_id}/docs/{doc_id}/"


def parsed_key(kb_id: int, doc_id: int, name: str, *, edited: bool = False) -> str:
    """解析结果键：`parsed/original/doc.json` 或 `parsed/edited/doc.json`。"""
    variant = "edited" if edited else "original"
    return doc_file_key(kb_id, doc_id, f"parsed/{variant}/{name}")


def images_prefix(kb_id: int, doc_id: int) -> str:
    """解析出的图片目录：`kb/{kb_id}/docs/{doc_id}/images/`。"""
    return doc_file_key(kb_id, doc_id, "images/")


def trial_prefix(trial_id: int) -> str:
    """入库方案试运行的临时目录：`tmp/trial/{trial_id}/`。"""
    return f"tmp/trial/{trial_id}/"


class Storage:
    """boto3 是同步库，所有调用都放到线程里执行，不阻塞事件循环（P3-02 补充）。"""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.S3_BUCKET
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT or None,
            aws_access_key_id=settings.S3_ACCESS_KEY or None,
            aws_secret_access_key=settings.S3_SECRET_KEY or None,
            region_name=settings.S3_REGION,
            # path-style：RustFS 等自建 S3 用路径寻址（与解析服务同一套）
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    async def _run(self, operation: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await anyio.to_thread.run_sync(lambda: func(*args, **kwargs))
        except (ClientError, BotoCoreError) as exc:
            # 只带操作名，不带连接串 / 凭据（开发约定 4.5）
            raise StorageError(detail={"operation": operation}) from exc

    async def upload_stream(
        self,
        key: str,
        fileobj: BinaryIO,
        content_type: str | None = None,
    ) -> None:
        """流式上传（boto3 自动分片），不把整个文件读进内存。"""
        extra = {"ContentType": content_type} if content_type else None
        await self._run(
            "upload_stream",
            self._client.upload_fileobj,
            fileobj,
            self._bucket,
            key,
            ExtraArgs=extra,
        )

    async def download(self, key: str, dest: str | Path) -> None:
        await self._run(
            "download",
            self._client.download_file,
            self._bucket,
            key,
            str(dest),
        )

    async def put_bytes(self, key: str, data: bytes) -> None:
        await self._run(
            "put_bytes",
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
        )

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

        return await self._run("get_bytes", _get)

    async def get_object(
        self,
        key: str,
        byte_range: str | None = None,
    ) -> tuple[Any, int | None, str | None]:
        """返回 `(body, content_length, content_range)`；`byte_range` 支持 HTTP Range。"""

        def _get() -> tuple[Any, int | None, str | None]:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
            if byte_range:
                kwargs["Range"] = byte_range
            response = self._client.get_object(**kwargs)
            return (
                response["Body"],
                response.get("ContentLength"),
                response.get("ContentRange"),
            )

        return await self._run("get_object", _get)

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError as exc:
                if _is_not_found(exc):
                    return False
                raise

        return await self._run("exists", _head)

    async def delete_prefix(self, prefix: str) -> int:
        """按前缀删除，返回删除的对象数（删除文档 / 知识库时用）。"""

        def _delete() -> int:
            deleted = 0
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if keys:
                    self._client.delete_objects(
                        Bucket=self._bucket,
                        Delete={"Objects": keys, "Quiet": True},
                    )
                    deleted += len(keys)
            return deleted

        return await self._run("delete_prefix", _delete)

    async def copy(self, source_key: str, dest_key: str) -> None:
        """同桶复制（两代切换 / 转换产物用）。"""
        await self._run(
            "copy",
            self._client.copy_object,
            Bucket=self._bucket,
            CopySource={"Bucket": self._bucket, "Key": source_key},
            Key=dest_key,
        )

    async def check(self) -> None:
        """桶存在且可访问；失败抛异常，`/readyz` 用。"""
        await self._run("check", self._client.head_bucket, Bucket=self._bucket)
