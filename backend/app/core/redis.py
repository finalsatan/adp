"""Redis 客户端。"""

from fastapi import Request
from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


def get_redis(request: Request) -> Redis:
    """FastAPI 依赖：应用共用的 Redis 客户端。"""
    return request.app.state.redis
