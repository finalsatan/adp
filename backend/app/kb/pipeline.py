"""入库流水线（知识库技术方案 8.1）。

P3-08 只提供 Celery 骨架需要的空实现；步骤 1（转换）、2（解析）在 P3-09 实现，
分块在 P3-10，向量化与两代切换在 P3-11。
"""

from __future__ import annotations

import logging

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


async def run_pipeline(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    doc_id: int,
    run_id: str | None,
) -> None:
    """执行一个文档的入库流水线（P3-08 空实现，P3-09 起实现步骤）。"""
    logger.info("入库流水线待实现（P3-09）: doc_id=%s run_id=%s", doc_id, run_id)
