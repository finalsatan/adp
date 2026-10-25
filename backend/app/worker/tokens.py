"""全局解析并发令牌（P3-08，知识库技术方案 8.2、8.3）。

转换和解析两步开始前先拿 Redis 里的全局解析并发令牌（租约方式，与 P2-04 的模型
并发限流同思路，用独立的键）；上限取系统设置 `upload.parse_concurrency`。拿不到时
流水线抛 `RetryLater`，由 `ingest_document` 稍后重投，**不算失败、不计自动重试
次数**（8.2、8.3）。

进程崩溃时不续约，租约到期后自动失效；续约、清理都用 P2-04 的原子脚本，时间取
Redis 的 `TIME`，不用各进程本地时间。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from uuid import uuid4

from redis.asyncio import Redis

from app.gateway.ratelimit import (
    _ACQUIRE_LUA,
    _RENEW_LUA,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_RENEW_INTERVAL,
)

logger = logging.getLogger(__name__)

# 全局解析并发的有序集合键（P2-04 的模型限流用 rl:conc:...，这里用独立的键）。
PARSE_CONCURRENCY_KEY = "parse:conc"
# 拿不到令牌后的重试间隔（知识库技术方案 8.2：10–30 秒）。
RETRY_MIN_SECONDS = 10.0
RETRY_MAX_SECONDS = 30.0


class ParseConcurrencyLimited(Exception):
    """全局解析并发已满（知识库技术方案 8.2）。"""


class RetryLater(Exception):
    """稍后重试：不算失败、不计自动重试次数（8.3）。"""

    def __init__(self, delay: float | None = None) -> None:
        self.delay = (
            random.uniform(RETRY_MIN_SECONDS, RETRY_MAX_SECONDS) if delay is None else delay
        )
        super().__init__(f"稍后重试（{self.delay:.1f} 秒）")


@dataclass
class ParseTokenLease:
    """一次已获取的解析并发租约。"""

    redis: Redis
    member_id: str
    lease_seconds: float = DEFAULT_LEASE_SECONDS
    renew_task: asyncio.Task[None] | None = field(default=None, repr=False)

    async def renew(self) -> None:
        """把自己的成员到期时间往后推；成员已被删除时不动。"""
        await self.redis.eval(
            _RENEW_LUA, 1, PARSE_CONCURRENCY_KEY, self.member_id, str(self.lease_seconds)
        )

    async def release(self) -> None:
        """从并发集合里删除自己。"""
        await self.redis.zrem(PARSE_CONCURRENCY_KEY, self.member_id)


class ParseTokenLimiter:
    """按上限获取全局解析并发令牌。线程安全（依赖 Redis 原子脚本）。"""

    def __init__(
        self,
        redis: Redis,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        renew_interval: float = DEFAULT_RENEW_INTERVAL,
    ) -> None:
        self._redis = redis
        self.lease_seconds = lease_seconds
        self.renew_interval = renew_interval

    async def acquire(self, limit: int) -> ParseTokenLease:
        """获取一个令牌；已满时抛 `ParseConcurrencyLimited`。"""
        if limit <= 0:
            raise ValueError("解析并发上限必须大于 0")
        member_id = uuid4().hex
        result = await self._redis.eval(
            _ACQUIRE_LUA,
            1,
            PARSE_CONCURRENCY_KEY,
            "0",  # 无 QPS 检查
            "1",  # 一个并发集合
            member_id,
            str(self.lease_seconds),
            str(limit),
        )
        if not int(result[0]):
            raise ParseConcurrencyLimited()
        return ParseTokenLease(
            redis=self._redis,
            member_id=member_id,
            lease_seconds=self.lease_seconds,
        )

    async def _renew_loop(self, lease: ParseTokenLease) -> None:
        while True:
            await asyncio.sleep(self.renew_interval)
            try:
                await lease.renew()
            except Exception:  # noqa: BLE001 - 续约失败只记日志，不打断流水线
                logger.warning("解析并发租约续约失败", exc_info=True)

    async def _finish(self, lease: ParseTokenLease) -> None:
        if lease.renew_task is not None:
            lease.renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease.renew_task
        await lease.release()

    @asynccontextmanager
    async def hold(self, limit: int) -> AsyncIterator[ParseTokenLease]:
        """获取令牌并在退出（含异常、取消）时释放。"""
        lease = await self.acquire(limit)
        lease.renew_task = asyncio.create_task(self._renew_loop(lease))
        try:
            yield lease
        finally:
            finish = asyncio.ensure_future(self._finish(lease))
            try:
                await asyncio.shield(finish)
            except asyncio.CancelledError:
                # 任务被取消时也要等清理完成，再继续传播取消。
                await finish
                raise
