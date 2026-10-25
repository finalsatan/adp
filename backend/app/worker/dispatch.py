"""按知识库公平的入库调度、排队位次、心跳与取消检查（P3-08，知识库技术方案 8.2、8.3）。

文档表就是队列的唯一事实来源：`dispatch(kb_id)` 先取该知识库的事务级咨询锁，
统计处理中的文档数，未达到“每个知识库最多占用的解析并发”时按入队时间用
`FOR UPDATE SKIP LOCKED` 取出排队中的文档，改为处理中并在事务提交后投递
`ingest_document`。

调用方（上传、文档处理结束、beat 补发）复用这里的函数；投递用可注入的 `send`
回调，便于测试和避免与 `tasks` 循环导入。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.kb.tables import Document
from app.settings import service as settings_service

logger = logging.getLogger(__name__)

# 处理中的状态：排队中和终态（done / failed）之外都算处理中（知识库技术方案 8.1）。
PROCESSING_STATUSES = ("parsing", "chunking", "enhancing", "embedding")
# 心跳超过 5 分钟没有更新的处理中文档，由 beat 放回调度（8.3，补充）。
HEARTBEAT_TIMEOUT_SECONDS = 300
# 咨询锁命名空间，避免与系统里其它 advisory lock 撞 key。
_LOCK_NAMESPACE = 1

SendTask = Callable[[int, str | None], None]


class RunCancelled(Exception):
    """文档的 `run_id` 已变化（删除 / 重新解析），正在跑的任务应退出（8.3）。"""


def _default_send(doc_id: int, run_id: str | None) -> None:
    """默认投递方式：发一个 `ingest_document` 到 `ingest` 队列。"""
    from app.worker.tasks import ingest_document

    ingest_document.apply_async(args=[doc_id, run_id], queue="ingest")


def _lock_key(kb_id: int) -> int:
    return (_LOCK_NAMESPACE << 32) | int(kb_id)


async def dispatch(
    sessionmaker: async_sessionmaker[AsyncSession],
    kb_id: int,
    *,
    send: SendTask | None = None,
) -> list[int]:
    """把一个知识库上能跑的排队文档改为处理中并投递；返回被投递的文档 id。"""
    send = send or _default_send
    async with sessionmaker() as session:
        settings = await settings_service.get_settings(session)
        limit = settings.upload.kb_parse_concurrency

        # 事务级咨询锁：同一知识库的并发 dispatch 串行化（8.2）。
        await session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _lock_key(kb_id)}
        )
        processing = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Document)
            .where(Document.kb_id == kb_id, Document.status.in_(PROCESSING_STATUSES))
        )
        slots = limit - int(processing or 0)
        if slots <= 0:
            await session.rollback()
            return []

        rows = (
            await session.execute(
                sa.select(Document.id, Document.run_id)
                .where(Document.kb_id == kb_id, Document.status == "queued")
                .order_by(Document.queued_at.asc().nulls_last(), Document.id.asc())
                .limit(slots)
                .with_for_update(skip_locked=True)
            )
        ).all()
        now = datetime.now(UTC)
        picked: list[tuple[int, str | None]] = []
        for doc_id, run_id in rows:
            await session.execute(
                sa.update(Document)
                .where(Document.id == doc_id)
                .values(status="parsing", started_at=now, heartbeat_at=now, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            picked.append((doc_id, run_id))
        await session.commit()

    # 投递在事务提交之后执行，避免任务读到未提交的状态（补充）。
    for doc_id, run_id in picked:
        send(doc_id, run_id)
    return [doc_id for doc_id, _ in picked]


async def queued_position(session: AsyncSession, doc: Document) -> int | None:
    """文档在本库排队文档中的名次（1 开始）；不是排队中时返回 None。"""
    if doc.status != "queued":
        return None
    earlier = sa.or_(
        Document.queued_at < doc.queued_at,
        sa.and_(Document.queued_at == doc.queued_at, Document.id < doc.id),
    )
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Document)
        .where(Document.kb_id == doc.kb_id, Document.status == "queued", earlier)
    )
    return int(count or 0) + 1


async def touch_heartbeat(session: AsyncSession, doc_id: int) -> None:
    """写 `documents.heartbeat_at`（流水线每一步调用，8.1）。"""
    await session.execute(
        sa.update(Document)
        .where(Document.id == doc_id)
        .values(heartbeat_at=sa.func.now())
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def check_run_id(session: AsyncSession, doc_id: int, run_id: str | None) -> None:
    """每步开始前调用；文档的 `run_id` 与任务携带的不一致时抛 `RunCancelled`（8.3）。"""
    current = await session.scalar(sa.select(Document.run_id).where(Document.id == doc_id))
    if current != run_id:
        raise RunCancelled(f"文档 {doc_id} 的 run_id 已变化")


async def requeue_stale(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    send: SendTask | None = None,
    now: datetime | None = None,
    timeout_seconds: int = HEARTBEAT_TIMEOUT_SECONDS,
) -> list[int]:
    """把心跳超时且处理中的文档放回排队，再投递它们所在的知识库（8.3）。"""
    send = send or _default_send
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(seconds=timeout_seconds)
    async with sessionmaker() as session:
        result = await session.execute(
            sa.update(Document)
            .where(
                Document.status.in_(PROCESSING_STATUSES),
                sa.or_(Document.heartbeat_at.is_(None), Document.heartbeat_at < cutoff),
            )
            .values(status="queued", started_at=None, heartbeat_at=None, updated_at=moment)
            .returning(Document.kb_id)
            .execution_options(synchronize_session=False)
        )
        kb_ids = list(result.scalars().all())
        await session.commit()

    for kb_id in dict.fromkeys(kb_ids):
        await dispatch(sessionmaker, kb_id, send=send)
    return kb_ids


async def redispatch_queued(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    send: SendTask | None = None,
) -> list[int]:
    """beat 兜底：对所有还有排队文档的知识库补发一次调度（8.2）。"""
    send = send or _default_send
    async with sessionmaker() as session:
        kb_ids = list(
            (
                await session.execute(
                    sa.select(Document.kb_id).where(Document.status == "queued").distinct()
                )
            )
            .scalars()
            .all()
        )
    for kb_id in kb_ids:
        await dispatch(sessionmaker, kb_id, send=send)
    return kb_ids
