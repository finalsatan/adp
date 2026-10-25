"""按知识库公平的调度、排队位次、心跳与取消检查（P3-08，知识库技术方案 8.2、8.3）。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.kb.tables import Document
from app.settings import service as settings_service
from app.worker.dispatch import (
    RunCancelled,
    check_run_id,
    dispatch,
    queued_position,
    redispatch_queued,
    requeue_stale,
)
from tests.worker import make_document, make_kb


async def _set_limits(session: AsyncSession, actor_id: int, kb_limit: int) -> None:
    await settings_service.update_settings(
        session, {"upload": {"kb_parse_concurrency": kb_limit}}, actor_id=actor_id
    )


async def _statuses(session: AsyncSession, kb_id: int) -> list[str]:
    result = await session.execute(sa.select(Document.status).where(Document.kb_id == kb_id))
    return list(result.scalars().all())


async def test_dispatch_fair_between_kbs(app, session: AsyncSession) -> None:
    """每库上限 1：两个库各自只调度 1 个，另一库被调度不受影响（19.3 按库公平）。"""
    kb_a, user_a = await make_kb(session, "库甲")
    kb_b, user_b = await make_kb(session, "库乙")
    base = datetime.now(UTC) - timedelta(minutes=10)
    for i in range(3):
        await make_document(
            session, kb_a.id, user_a, name=f"甲{i}.pdf", queued_at=base + timedelta(seconds=i)
        )
    await make_document(session, kb_b.id, user_b, name="乙0.pdf", queued_at=base)
    await _set_limits(session, user_a, 1)

    sent: list[tuple[int, int]] = []
    await dispatch(app.state.sessionmaker, kb_a.id, send=lambda d, r: sent.append((kb_a.id, d)))
    await dispatch(app.state.sessionmaker, kb_b.id, send=lambda d, r: sent.append((kb_b.id, d)))

    assert len(sent) == 2
    assert sorted(await _statuses(session, kb_a.id)) == ["parsing", "queued", "queued"]
    assert sorted(await _statuses(session, kb_b.id)) == ["parsing"]


async def test_dispatch_concurrent_does_not_overdispatch(app, session: AsyncSession) -> None:
    """并发调用同一知识库的 dispatch 不会超发（咨询锁 + SKIP LOCKED）。"""
    kb, user = await make_kb(session, "并发库")
    for i in range(5):
        await make_document(session, kb.id, user, name=f"并发{i}.pdf")
    await _set_limits(session, user, 1)

    sent: list[int] = []

    async def _one() -> None:
        await dispatch(app.state.sessionmaker, kb.id, send=lambda d, r: sent.append(d))

    await asyncio.gather(*(_one() for _ in range(5)))

    assert len(sent) == 1
    assert sorted(await _statuses(session, kb.id)) == [
        "parsing",
        "queued",
        "queued",
        "queued",
        "queued",
    ]


async def test_queued_position_by_queued_at(session: AsyncSession) -> None:
    kb, user = await make_kb(session, "位次库")
    base = datetime.now(UTC) - timedelta(minutes=5)
    doc_ids = [
        await make_document(
            session, kb.id, user, name=f"位{i}.pdf", queued_at=base + timedelta(seconds=i)
        )
        for i in range(3)
    ]

    positions = []
    for doc_id in doc_ids:
        doc = await session.get(Document, doc_id)
        assert doc is not None
        positions.append(await queued_position(session, doc))
    assert positions == [1, 2, 3]

    # 处理中的文档没有排队位次。
    processing_id = await make_document(session, kb.id, user, name="处理中.pdf", status="parsing")
    processing = await session.get(Document, processing_id)
    assert processing is not None
    assert await queued_position(session, processing) is None


async def test_requeue_stale_puts_back_and_dispatches(app, session: AsyncSession) -> None:
    """心跳超过阈值后放回排队并重新投递；未超时的处理中文档不动。"""
    kb, user = await make_kb(session, "心跳库")
    old = datetime.now(UTC) - timedelta(minutes=10)
    fresh = datetime.now(UTC)
    stale_id = await make_document(
        session, kb.id, user, name="超时.pdf", status="parsing", heartbeat_at=old
    )
    fresh_id = await make_document(
        session, kb.id, user, name="新鲜.pdf", status="parsing", heartbeat_at=fresh
    )
    await _set_limits(session, user, 2)

    sent: list[int] = []
    await requeue_stale(app.state.sessionmaker, send=lambda d, r: sent.append(d))

    # 超时的被放回调度后又变成处理中，且被投递；新鲜的不动。
    assert sent == [stale_id]
    stale = (
        await session.execute(
            sa.select(Document.status, Document.started_at).where(Document.id == stale_id)
        )
    ).one()
    fresh = (
        await session.execute(sa.select(Document.status).where(Document.id == fresh_id))
    ).scalar_one()
    assert stale.status == "parsing" and stale.started_at is not None
    assert fresh == "parsing"


async def test_redispatch_queued_dispatches_kbs_with_queue(app, session: AsyncSession) -> None:
    kb, user = await make_kb(session, "补发库")
    await make_document(session, kb.id, user, name="排队.pdf")
    await _set_limits(session, user, 1)

    sent: list[int] = []
    kb_ids = await redispatch_queued(app.state.sessionmaker, send=lambda d, r: sent.append(d))
    assert kb_ids == [kb.id]
    assert len(sent) == 1
    assert sorted(await _statuses(session, kb.id)) == ["parsing"]


async def test_check_run_id_raises_after_change(session: AsyncSession) -> None:
    kb, user = await make_kb(session, "取消库")
    doc_id = await make_document(session, kb.id, user, name="取消.pdf", run_id="run-1")

    await check_run_id(session, doc_id, "run-1")
    with pytest.raises(RunCancelled):
        await check_run_id(session, doc_id, "run-2")
    # 文档被删除（查不到）也视为已取消。
    with pytest.raises(RunCancelled):
        await check_run_id(session, doc_id + 99999, "run-1")
