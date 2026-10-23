"""模型调用限流测试（P2-04，技术方案 9.3、13.2）。连真实 Redis（15 号库）。"""

import asyncio
import time
from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
from redis.asyncio import Redis

from app.gateway.ratelimit import RateLimited, RateLimiter


def make_model(
    *, rate_qps: int | None = None, rate_concurrency: int | None = None, model_id: int = 1
):
    return SimpleNamespace(id=model_id, rate_qps=rate_qps, rate_concurrency=rate_concurrency)


async def _wait_next_second(redis: Redis) -> None:
    """等到 Redis 的下一个整秒边界，保证随后的几次调用落在同一秒。"""
    _, usec = await redis.time()
    await asyncio.sleep(1.0 - int(usec) / 1_000_000 + 0.02)


async def test_qps_limit_and_recover(redis: Redis) -> None:
    """QPS 为 3：同一秒内第 4 次被拒，下一秒恢复。"""
    model = make_model(rate_qps=3)
    limiter = RateLimiter(redis)

    await _wait_next_second(redis)
    for _ in range(3):
        async with limiter.acquire(model):
            pass

    with pytest.raises(RateLimited) as exc:
        async with limiter.acquire(model):
            pass
    assert exc.value.scope == "total"

    await _wait_next_second(redis)
    async with limiter.acquire(model):
        pass


async def test_concurrency_limit_under_load(redis: Redis) -> None:
    """并发为 5，50 个任务同时抢：任何时刻集合不超过 5，超出的抛 RateLimited。"""
    model = make_model(rate_concurrency=5)
    limiter = RateLimiter(redis)
    key = f"rl:conc:{model.id}:total"

    sizes: list[int] = []
    accepted = 0
    rejected = 0

    async def worker() -> None:
        nonlocal accepted, rejected
        try:
            async with limiter.acquire(model):
                sizes.append(await redis.zcard(key))
                await asyncio.sleep(0.2)
            accepted += 1
        except RateLimited:
            rejected += 1

    await asyncio.gather(*(worker() for _ in range(50)))

    assert max(sizes) <= 5
    assert accepted <= 5
    assert rejected > 0


async def test_lease_renewal_and_expiry(redis: Redis) -> None:
    """租约 1 秒、续约 0.3 秒：持有 3 秒成员一直在；不再续约的成员会过期被清掉。"""
    model = make_model(rate_concurrency=1)
    limiter = RateLimiter(redis, lease_seconds=1.0, renew_interval=0.3)
    key = f"rl:conc:{model.id}:total"

    async with limiter.acquire(model) as lease:
        for _ in range(6):
            await asyncio.sleep(0.5)
            assert await redis.zcard(key) == 1
            assert await redis.zscore(key, lease.member_id) is not None
    assert await redis.zcard(key) == 0

    # 模拟进程崩溃后不再续约：成员在租约到期后失效，下一次获取时被清理。
    await redis.zadd(key, {"crashed": time.time() - 1})
    async with limiter.acquire(model):
        assert await redis.zcard(key) == 1


async def test_ingest_quota_shares_total(redis: Redis) -> None:
    """入库比例 0.5、并发 4：入库最多 2，在线还能 2，合计不超过 4。"""
    model = make_model(rate_concurrency=4)
    limiter = RateLimiter(redis, ingest_ratio=0.5)
    total_key = f"rl:conc:{model.id}:total"
    ingest_key = f"rl:conc:{model.id}:ingest"

    async with limiter.acquire(model, ingest=True), limiter.acquire(model, ingest=True):
        assert await redis.zcard(ingest_key) == 2
        # 入库额度已满
        with pytest.raises(RateLimited) as exc:
            async with limiter.acquire(model, ingest=True):
                pass
        assert exc.value.scope == "ingest"

        # 在线还能用总额度剩下的两个
        async with limiter.acquire(model), limiter.acquire(model):
            assert await redis.zcard(total_key) == 4
            with pytest.raises(RateLimited) as exc2:
                async with limiter.acquire(model):
                    pass
            assert exc2.value.scope == "total"
            with pytest.raises(RateLimited):
                async with limiter.acquire(model, ingest=True):
                    pass

    # 在线占满总额度时，入库也进不去
    async with AsyncExitStack() as stack:
        for _ in range(4):
            await stack.enter_async_context(limiter.acquire(model))
        with pytest.raises(RateLimited):
            async with limiter.acquire(model, ingest=True):
                pass


async def test_ingest_quota_at_least_one(redis: Redis) -> None:
    """比例 0.1、并发 3：入库额度为 1 而不是 0。"""
    model = make_model(rate_concurrency=3)
    limiter = RateLimiter(redis, ingest_ratio=0.1)

    async with limiter.acquire(model, ingest=True):
        with pytest.raises(RateLimited) as exc:
            async with limiter.acquire(model, ingest=True):
                pass
    assert exc.value.scope == "ingest"


class _ExplodingRedis:
    """任何属性访问都报错，用来证明未设限流时不碰 Redis。"""

    def __getattr__(self, name: str):
        raise AssertionError(f"未设限流不应访问 Redis：{name}")


async def test_no_limit_does_not_touch_redis() -> None:
    """rate_qps、rate_concurrency 都为空时不做限流，也不访问 Redis。"""
    limiter = RateLimiter(_ExplodingRedis(), ingest_ratio=0.5)  # type: ignore[arg-type]
    model = make_model()

    async with limiter.acquire(model) as lease:
        assert lease.member_id is None
    async with limiter.acquire(model, ingest=True) as lease:
        assert lease.member_id is None


async def test_cancelled_task_releases_lease(redis: Redis) -> None:
    """任务被取消时租约被释放。"""
    model = make_model(rate_concurrency=1)
    limiter = RateLimiter(redis)
    key = f"rl:conc:{model.id}:total"
    started = asyncio.Event()

    async def hold() -> None:
        async with limiter.acquire(model):
            started.set()
            await asyncio.sleep(30)

    task = asyncio.create_task(hold())
    await started.wait()
    assert await redis.zcard(key) == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await redis.zcard(key) == 0
