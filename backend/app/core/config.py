"""应用配置：读环境变量和 backend/.env。"""

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
