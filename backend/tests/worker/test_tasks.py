"""worker 任务、稍后重试与常驻事件循环（P3-08，知识库技术方案 8.2、8.3、19.3）。"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.kb.tables import Document
from app.worker import loop, tasks
from app.worker.tokens import ParseConcurrencyLimited, ParseTokenLimiter, RetryLater
from tests.worker import make_document, make_kb


@pytest.fixture(scope="module")
def worker_context(settings):
    """一个常驻事件循环与连接，模拟 worker 子进程（module 级复用）。"""
    context = loop.init(settings)
    yield context
    loop.shutdown()


async def _create_document(context, *, run_id: str = "run-1") -> int:
    async with context.sessionmaker() as session:
        kb, user_id = await make_kb(session, "任务库")
        return await make_document(session, kb.id, user_id, name="任务.pdf", run_id=run_id)


async def _status(context, doc_id: int) -> str:
    async with context.sessionmaker() as session:
        return await session.scalar(sa.select(Document.status).where(Document.id == doc_id))


def test_celery_app_queues_and_reliability_settings() -> None:
    """三类队列、acks_late、beat 补发计划与任务归属正确。"""
    from app.worker.celery_app import celery_app

    assert {q.name for q in celery_app.conf.task_queues} == {
        "ingest",
        "maintenance",
        "interactive",
    }
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.tasks["app.worker.tasks.ingest_document"].queue == "ingest"
    assert celery_app.tasks["app.worker.tasks.dispatch_kb"].queue == "ingest"
    assert celery_app.tasks["app.worker.tasks.requeue_stale"].queue == "maintenance"
    assert celery_app.tasks["app.worker.tasks.redispatch_queued"].queue == "maintenance"
    assert set(celery_app.conf.beat_schedule) == {"requeue-stale", "redispatch-queued"}


def test_worker_loop_survives_multiple_and_failed_tasks(worker_context, monkeypatch) -> None:
    """同一子进程连续执行多个任务（含失败）后，连接池和 Redis 仍可用（19.3）。"""
    doc_id = loop.run(_create_document(worker_context))

    tasks._run_ingest(doc_id, "run-1")
    tasks._run_ingest(doc_id, "run-1")

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("流水线炸了")

    monkeypatch.setattr(tasks, "run_pipeline", _boom)
    with pytest.raises(RuntimeError):
        tasks._run_ingest(doc_id, "run-1")
    monkeypatch.undo()

    # 失败之后同一个循环、同一个连接池仍能跑任务，Redis 也还在。
    tasks._run_ingest(doc_id, "run-1")
    assert loop.run(worker_context.redis.ping()) is True


def test_retry_later_requeues_without_failure(worker_context, monkeypatch) -> None:
    """RetryLater 只重投、不写失败状态、不计自动重试次数。"""
    doc_id = loop.run(_create_document(worker_context, run_id="run-r"))
    retried: list[tuple[int, str | None, float, int]] = []
    monkeypatch.setattr(
        tasks,
        "_retry_ingest",
        lambda d, r, delay, retries: retried.append((d, r, delay, retries)),
    )

    async def _pipeline(*_args, **_kwargs):
        raise RetryLater(delay=12.5)

    monkeypatch.setattr(tasks, "run_pipeline", _pipeline)
    tasks._run_ingest(doc_id, "run-r", retries=0)

    assert retried == [(doc_id, "run-r", 12.5, 0)]
    assert loop.run(_status(worker_context, doc_id)) != "failed"


def test_global_parse_token_retries_later(worker_context, monkeypatch) -> None:
    """全局解析并发为 1：第二个任务拿不到令牌、稍后重试，不算失败。"""
    doc_id = loop.run(_create_document(worker_context, run_id="run-t"))
    delays: list[float] = []
    monkeypatch.setattr(tasks, "_retry_ingest", lambda d, r, delay, retries: delays.append(delay))

    async def _pipeline(_sessionmaker, redis, _doc_id, _run_id):
        limiter = ParseTokenLimiter(redis)
        try:
            async with limiter.hold(1):
                pass
        except ParseConcurrencyLimited as exc:
            raise RetryLater() from exc

    monkeypatch.setattr(tasks, "run_pipeline", _pipeline)

    # 先占住唯一的令牌，模拟另一个文档正在解析。
    limiter = ParseTokenLimiter(worker_context.redis)
    lease = loop.run(limiter.acquire(1))
    try:
        tasks._run_ingest(doc_id, "run-t")
    finally:
        loop.run(lease.release())

    assert len(delays) == 1
    assert 10.0 <= delays[0] <= 30.0
    assert loop.run(_status(worker_context, doc_id)) != "failed"

    # 令牌释放后再跑，不再重投。
    tasks._run_ingest(doc_id, "run-t")
    assert len(delays) == 1


def test_run_cancelled_task_exits(worker_context, monkeypatch) -> None:
    """run_id 变化时任务静默退出，不重投也不标失败。"""
    from app.worker.dispatch import RunCancelled

    doc_id = loop.run(_create_document(worker_context, run_id="run-c"))
    retried: list[object] = []
    monkeypatch.setattr(tasks, "_retry_ingest", lambda *args: retried.append(args))

    async def _pipeline(*_args, **_kwargs):
        raise RunCancelled("已取消")

    monkeypatch.setattr(tasks, "run_pipeline", _pipeline)
    tasks._run_ingest(doc_id, "run-c")

    assert retried == []
    assert loop.run(_status(worker_context, doc_id)) != "failed"
