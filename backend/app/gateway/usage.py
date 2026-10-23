"""model_usage 写入（技术方案 6.3、9.3、9.6）。

每次模型调用（成功、失败、取消）写一条，用**独立的数据库会话**，不受调用方事务
影响；写入放在 `finally` 里并用 `asyncio.shield` 保护，取消时也能写完。写入失败
只记 ERROR 日志，不影响调用结果。
"""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.tables import ModelUsage

logger = logging.getLogger(__name__)


@dataclass
class UsageRecord:
    model_id: int | None
    model_name: str
    user_id: int | None
    org_id: int | None
    source: str
    source_id: int | None
    input_tokens: int | None
    output_tokens: int | None
    first_token_ms: int | None
    total_ms: int | None
    ok: bool
    error_code: str | None


async def _insert(sessionmaker: async_sessionmaker[AsyncSession], record: UsageRecord) -> None:
    async with sessionmaker() as session:
        session.add(ModelUsage(**vars(record)))
        await session.commit()


async def record(
    sessionmaker: async_sessionmaker[AsyncSession] | None, record: UsageRecord
) -> None:
    """独立会话写入一条用量；取消时也保证写完。"""
    if sessionmaker is None:  # pragma: no cover - 调用方总会传入会话工厂
        logger.error("用量记录会话未配置，记录丢失: source=%s", record.source)
        return

    task = asyncio.ensure_future(_insert(sessionmaker, record))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # 调用方任务被取消时也要等写入完成，再继续传播取消。
        with suppress(Exception):
            await task
        raise
    except Exception:
        logger.error("用量记录写入失败: source=%s", record.source, exc_info=True)
