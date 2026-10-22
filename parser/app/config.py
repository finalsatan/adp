"""解析服务的配置，全部来自环境变量。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """解析服务运行配置。

    字段名会自动映射为同名的大写环境变量（如 ``parser_token`` → ``PARSER_TOKEN``）。
    """

    model_config = SettingsConfigDict(extra="ignore")

    # 接口认证令牌，Bearer 认证用
    parser_token: str = ""
    # 常驻解析子进程数
    parser_workers: int = 2
    # 排队作业上限，达到后提交返回 429
    parser_queue_max: int = 50
    # 单个作业超时时间（秒）
    parser_job_timeout_s: int = 1800
    # 单个文档最大页数
    parser_max_pages: int = 2000
    # 对象存储
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    # 只在测试中开启的假引擎
    parser_enable_fake_engine: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
