"""应用配置：读环境变量和 backend/.env。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
