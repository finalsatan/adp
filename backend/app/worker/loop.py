"""worker 子进程的常驻事件循环与连接（知识库技术方案 8.2 补充）。

主服务的数据库访问、Redis 客户端都是异步的，而 Celery 任务是同步函数。做法是
prefork 子进程启动时（`worker_process_init`）创建一个常驻事件循环，并在这个循环
里建本进程自己的数据库引擎和 Redis 客户端；子进程退出时关闭它们。

任务函数只用 `run()`（内部是 `loop.run_until_complete()`）执行异步步骤，**不用**
`asyncio.run()`：连接池绑定在创建它的循环上，换循环会出错。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import create_engine, create_sessionmaker
from app.core.redis import create_redis


@dataclass
class WorkerContext:
    """一个 worker 子进程自己的事件循环、数据库引擎和 Redis 客户端。"""

    loop: asyncio.AbstractEventLoop
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis


_context: WorkerContext | None = None


def init(settings: Settings | None = None) -> WorkerContext:
    """建常驻事件循环与连接；重复调用返回已有的上下文。"""
    global _context
    if _context is not None:
        return _context
    resolved = settings or get_settings()
    loop = asyncio.new_event_loop()
    engine = create_engine(resolved.DATABASE_URL)
    _context = WorkerContext(
        loop=loop,
        engine=engine,
        sessionmaker=create_sessionmaker(engine),
        redis=create_redis(resolved.REDIS_URL),
    )
    return _context


def get_context() -> WorkerContext:
    """取本进程的上下文；未初始化时按需创建（便于测试与直接调用）。"""
    if _context is None:
        return init()
    return _context


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """在常驻循环里执行一个协程并返回结果。"""
    return get_context().loop.run_until_complete(coro)


def shutdown() -> None:
    """关闭本进程的 Redis 客户端、数据库引擎和事件循环。"""
    global _context
    if _context is None:
        return
    context, _context = _context, None
    context.loop.run_until_complete(context.redis.aclose())
    context.loop.run_until_complete(context.engine.dispose())
    context.loop.close()
