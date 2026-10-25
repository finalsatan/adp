"""全局解析并发令牌（P3-08，知识库技术方案 8.2、8.3）。"""

from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis

from app.worker.tokens import (
    PARSE_CONCURRENCY_KEY,
    ParseConcurrencyLimited,
    ParseTokenLimiter,
)


async def test_limit_and_release(redis: Redis) -> None:
    """上限 1：第二个拿不到；释放后可再拿。"""
    limiter = ParseTokenLimiter(redis)

    async with limiter.hold(1):
        assert await redis.zcard(PARSE_CONCURRENCY_KEY) == 1
        with pytest.raises(ParseConcurrencyLimited):
            await limiter.acquire(1)

    assert await redis.zcard(PARSE_CONCURRENCY_KEY) == 0
    async with limiter.hold(1):
        pass


async def test_lease_expiry_without_renewal(redis: Redis) -> None:
    """进程崩溃不续约，租约到期后自动失效（沿用 P2-04 的到期清理）。"""
    limiter = ParseTokenLimiter(redis, lease_seconds=0.5)
    lease = await limiter.acquire(1)

    with pytest.raises(ParseConcurrencyLimited):
        await limiter.acquire(1)

    await asyncio.sleep(0.7)
    async with limiter.hold(1):
        pass
    await lease.release()


async def test_renewal_keeps_token(redis: Redis) -> None:
    """租约 1 秒、续约 0.3 秒：持有 1.5 秒期间令牌一直有效。"""
    limiter = ParseTokenLimiter(redis, lease_seconds=1.0, renew_interval=0.3)
    async with limiter.hold(1):
        for _ in range(5):
            await asyncio.sleep(0.3)
            assert await redis.zcard(PARSE_CONCURRENCY_KEY) == 1
        with pytest.raises(ParseConcurrencyLimited):
            await limiter.acquire(1)
