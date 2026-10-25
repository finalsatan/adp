"""Celery 任务（P3-08，知识库技术方案 8.2、8.3）。

任务函数是同步的，只用 `loop.run()`（`run_until_complete`）执行异步步骤，不用
`asyncio.run()`：连接池绑定在创建它的循环上。入库的具体步骤由 `app/kb/pipeline.py`
实现，P3-09 起补全。
"""

from __future__ import annotations

import logging

from app.kb import indexing
from app.kb.pipeline import run_pipeline
from app.worker import dispatch as dispatch_module
from app.worker import loop
from app.worker.celery_app import celery_app
from app.worker.dispatch import RunCancelled
from app.worker.tokens import RetryLater

logger = logging.getLogger(__name__)


def _send_ingest(doc_id: int, run_id: str | None) -> None:
    ingest_document.apply_async(args=[doc_id, run_id], queue="ingest")


def _retry_ingest(doc_id: int, run_id: str | None, delay: float, retries: int) -> None:
    """稍后重投：显式带上原来的 retries，不增加自动重试次数（8.2、8.3）。"""
    ingest_document.apply_async(
        args=[doc_id, run_id], countdown=delay, retries=retries, queue="ingest"
    )


def _run_ingest(doc_id: int, run_id: str | None, *, retries: int = 0) -> None:
    context = loop.get_context()
    try:
        context.loop.run_until_complete(
            run_pipeline(context.sessionmaker, context.redis, doc_id, run_id)
        )
    except RetryLater as exc:
        logger.info("入库任务稍后重试: doc_id=%s delay=%.1f", doc_id, exc.delay)
        _retry_ingest(doc_id, run_id, exc.delay, retries)
    except RunCancelled:
        logger.info("入库任务已取消: doc_id=%s", doc_id)


@celery_app.task(bind=True, name="app.worker.tasks.ingest_document", queue="ingest")
def ingest_document(self, doc_id: int, run_id: str | None) -> None:
    """执行一个文档的入库流水线。"""
    _run_ingest(doc_id, run_id, retries=self.request.retries)


@celery_app.task(name="app.worker.tasks.dispatch_kb", queue="ingest")
def dispatch_kb(kb_id: int) -> None:
    """调度一个知识库上的排队文档（上传后、处理结束时调用）。"""
    context = loop.get_context()
    context.loop.run_until_complete(
        dispatch_module.dispatch(context.sessionmaker, kb_id, send=_send_ingest)
    )


@celery_app.task(name="app.worker.tasks.requeue_stale", queue="maintenance")
def requeue_stale() -> None:
    """beat 每分钟：把心跳超时的处理中文档放回调度。"""
    context = loop.get_context()
    context.loop.run_until_complete(
        dispatch_module.requeue_stale(context.sessionmaker, send=_send_ingest)
    )


@celery_app.task(name="app.worker.tasks.redispatch_queued", queue="maintenance")
def redispatch_queued() -> None:
    """beat 每 30 秒：对所有有排队文档的知识库补发一次调度。"""
    context = loop.get_context()
    context.loop.run_until_complete(
        dispatch_module.redispatch_queued(context.sessionmaker, send=_send_ingest)
    )


@celery_app.task(name="app.worker.tasks.cleanup_old_generations", queue="maintenance")
def cleanup_old_generations() -> None:
    """beat：删除已生效文档的旧一代分块与索引条目（P3-12，知识库技术方案 8.4）。"""
    context = loop.get_context()
    cleaned = context.loop.run_until_complete(
        indexing.cleanup_old_generations(context.sessionmaker)
    )
    if cleaned:
        logger.info("维护任务 cleanup_old_generations 清理了 %s 个文档", cleaned)
