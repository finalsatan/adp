"""应用配置：读环境变量和 backend/.env。"""

import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _derive_celery_broker(redis_url: str) -> str:
    """从 REDIS_URL 派生 Celery 用的 1 号库地址（P3-02 补充）。"""
    parts = urlsplit(redis_url)
    if not parts.scheme:
        return redis_url
    return urlunsplit((parts.scheme, parts.netloc, "/1", parts.query, parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: Literal["dev", "test", "prod"] = "dev"
    DATABASE_URL: str
    MIGRATION_DATABASE_URL: str = ""
    REDIS_URL: str
    JWT_SECRET: str = ""
    # 模型密钥的加密主密钥：Base64 编码的 32 字节（技术方案 9.2）。
    MASTER_KEY: str = ""
    COOKIE_SECURE: bool = True
    LOG_LEVEL: str = "INFO"

    # 对象存储（S3 兼容，RustFS）：知识库技术方案 18。
    S3_ENDPOINT: str = ""
    S3_BUCKET: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_REGION: str = "us-east-1"
    # 解析服务（知识库技术方案 6.1）。
    PARSER_URL: str = ""
    PARSER_TOKEN: str = ""
    # Celery 消息队列（Redis 的另一个库号）；为空时从 REDIS_URL 派生为 1 号库。
    CELERY_BROKER_URL: str = ""

    @field_validator("JWT_SECRET")
    @classmethod
    def _check_jwt_secret(cls, value: str) -> str:
        if len(value.encode("utf-8")) < 32:
            raise ValueError(
                "JWT_SECRET 不能为空且至少 32 字节，请在环境变量或 backend/.env 中设置"
            )
        return value

    @field_validator("MASTER_KEY")
    @classmethod
    def _check_master_key(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "MASTER_KEY 不能为空，必须是 Base64 编码的 32 字节主密钥；"
                "请在环境变量或 backend/.env 中设置"
            )
        try:
            raw = base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise ValueError("MASTER_KEY 必须是 Base64 编码的 32 字节主密钥") from exc
        if len(raw) != 32:
            raise ValueError("MASTER_KEY 解码后必须是 32 字节")
        return value

    @model_validator(mode="after")
    def _apply_derived_and_prod_checks(self) -> "Settings":
        # Celery 消息队列默认用 Redis 的 1 号库（P3-02 补充）。
        if not self.CELERY_BROKER_URL:
            self.CELERY_BROKER_URL = _derive_celery_broker(self.REDIS_URL)
        # 生产环境缺对象存储 / 解析服务地址时启动失败（P3-02 补充）。
        if self.APP_ENV == "prod":
            missing = [
                name
                for name in ("S3_ENDPOINT", "S3_BUCKET", "PARSER_URL")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    "生产环境缺少必需的环境变量："
                    + "、".join(missing)
                    + "；请在环境变量或 deploy/.env 中设置"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
