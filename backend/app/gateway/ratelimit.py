"""模型调用限流（P2-04，技术方案 9.3、需求 3.5）。

每个模型有两组额度：

- 总额度：QPS = `rate_qps`，并发 = `rate_concurrency`。
- 入库额度：总额度 × 系统设置 `model_call.ingest_rate_ratio`（向下取整，至少 1）。

在线调用只占总额度；入库调用（`ingest=True`）两组都占，两组都未超限才放行。
模型的 `rate_qps`、`rate_concurrency` 都为空时不做任何限流，也不访问 Redis。

用法：

    limiter = RateLimiter(redis, ingest_ratio=settings.model_call.ingest_rate_ratio)
    async with limiter.acquire(model, ingest=False) as lease:
        ...  # 调用模型；流式输出期间 lease 自动每 20 秒续约

超限时抛 `RateLimited(scope)`，调用方决定怎么处理：在线调用转成
`MODEL_RATE_LIMITED`，入库调用稍后重试（第三期）。

时间一律用 Redis 的 `TIME`，不用各 api 进程的本地时间；续约、清理都是原子的。
进程崩溃时不续约，租约最多 `lease_seconds` 后失效。
"""

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_LUA_DIR = Path(__file__).resolve().parent / "lua"
_ACQUIRE_LUA = (_LUA_DIR / "acquire.lua").read_text(encoding="utf-8")
_RENEW_LUA = (_LUA_DIR / "renew.lua").read_text(encoding="utf-8")

DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_RENEW_INTERVAL = 20.0


class RateLimitTarget(Protocol):
    """限流需要的模型字段；`models` 表的 ORM 对象满足这个协议。"""

    id: int
    rate_qps: int | None
    rate_concurrency: int | None


class RateLimited(Exception):
    """超限。`scope` 为 `"total"`（总额度）或 `"ingest"`（入库额度）。"""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        super().__init__(f"模型调用超限：{scope}")


@dataclass
class Lease:
    """一次已获取的并发租约。

    `member_id` 为空表示模型未设并发限流（或两组额度都为空），此时没有需要
    续约、释放的成员。退出上下文时由 `RateLimiter` 负责释放。
    """

    redis: Redis | None = None
    member_id: str | None = None
    conc_keys: tuple[str, ...] = ()
    lease_seconds: float = DEFAULT_LEASE_SECONDS
    renew_task: asyncio.Task[None] | None = field(default=None, repr=False)

    async def renew(self) -> None:
        """把自己的成员到期时间往后推；成员已被删除时不动。"""
        if self.member_id is None or self.redis is None:
            return
        for key in self.conc_keys:
            await self.redis.eval(_RENEW_LUA, 1, key, self.member_id, str(self.lease_seconds))

    async def release(self) -> None:
        """从所有并发集合里删除自己。"""
        if self.member_id is None or self.redis is None:
            return
        for key in self.conc_keys:
            await self.redis.zrem(key, self.member_id)


class RateLimiter:
    """按模型获取 QPS / 并发额度。线程安全（依赖 Redis 原子脚本）。"""

    def __init__(
        self,
        redis: Redis,
        *,
        ingest_ratio: float = 0.5,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        renew_interval: float = DEFAULT_RENEW_INTERVAL,
    ) -> None:
        self._redis = redis
        self._ingest_ratio = ingest_ratio
        self.lease_seconds = lease_seconds
        self.renew_interval = renew_interval

    def _limit(self, total: int, scope: str) -> int:
        """总额度原样返回；入库额度按比例折算，至少 1。"""
        if scope == "total":
            return total
        return max(1, math.floor(total * self._ingest_ratio))

    def _checks(
        self, model: RateLimitTarget, ingest: bool
    ) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int]]]:
        """返回 (QPS 检查, 并发检查)，每项是 (scope, key, limit)。"""
        scopes: Iterable[str] = ("total", "ingest") if ingest else ("total",)
        qps: list[tuple[str, str, int]] = []
        conc: list[tuple[str, str, int]] = []
        if model.rate_qps is not None:
            for scope in scopes:
                qps.append(
                    (scope, f"rl:qps:{model.id}:{scope}:", self._limit(model.rate_qps, scope))
                )
        if model.rate_concurrency is not None:
            for scope in scopes:
                conc.append(
                    (
                        scope,
                        f"rl:conc:{model.id}:{scope}",
                        self._limit(model.rate_concurrency, scope),
                    )
                )
        return qps, conc

    async def _try_acquire(self, model: RateLimitTarget, ingest: bool) -> Lease:
        qps, conc = self._checks(model, ingest)
        if not qps and not conc:
            return Lease()

        member_id = uuid4().hex
        keys = [key for _, key, _ in qps] + [key for _, key, _ in conc]
        args = [
            str(len(qps)),
            str(len(conc)),
            member_id,
            str(self.lease_seconds),
            *(str(limit) for _, _, limit in qps),
            *(str(limit) for _, _, limit in conc),
        ]
        result = await self._redis.eval(_ACQUIRE_LUA, len(keys), *keys, *args)
        ok, kind, index = int(result[0]), str(result[1]), int(result[2])
        if not ok:
            checks = qps if kind == "qps" else conc
            raise RateLimited(checks[index - 1][0])

        return Lease(
            redis=self._redis,
            member_id=member_id,
            conc_keys=tuple(key for _, key, _ in conc),
            lease_seconds=self.lease_seconds,
        )

    async def _renew_loop(self, lease: Lease) -> None:
        while True:
            await asyncio.sleep(self.renew_interval)
            try:
                await lease.renew()
            except Exception:  # noqa: BLE001 - 续约失败只记日志，不打断调用
                logger.warning("模型调用租约续约失败", exc_info=True)

    async def _finish(self, lease: Lease) -> None:
        """退出时的清理：停止续约并释放；清理本身不能被取消打断。"""
        if lease.renew_task is not None:
            lease.renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease.renew_task
        await lease.release()

    @asynccontextmanager
    async def acquire(self, model: RateLimitTarget, ingest: bool = False) -> AsyncIterator[Lease]:
        """获取额度；超限抛 `RateLimited`。退出（含异常、取消）时释放。"""
        lease = await self._try_acquire(model, ingest)
        if lease.member_id is None:
            yield lease
            return

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
