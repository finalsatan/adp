"""应用配置：读环境变量和 backend/.env。"""

import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
