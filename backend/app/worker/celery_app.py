"""Celery 应用：broker、三类队列、beat 与子进程事件循环（P3-08，知识库技术方案 8.2、18）。

- `ingest`、`maintenance` 由 worker 消费；`interactive` 由 worker-interactive 消费。
- 开启 `acks_late`、`task_reject_on_worker_lost`：worker 崩溃时任务被重新投递（8.3）。
- 子进程启动时建常驻事件循环与连接（`app/worker/loop.py`）。
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown
from kombu import Queue

from app.core.config import get_settings
from app.worker import loop

logger = logging.getLogger(__name__)

settings = get_settings()

celery_app = Celery(
    "adp",
    broker=settings.CELERY_BROKER_URL,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_default_queue="ingest",
    task_queues=(
        Queue("ingest"),
        Queue("maintenance"),
        Queue("interactive"),
    ),
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        # 心跳超时的文档放回调度（每分钟）。
        "requeue-stale": {
            "task": "app.worker.tasks.requeue_stale",
            "schedule": 60.0,
        },
        # 对所有还有排队文档的知识库补发（每 30 秒，兜底）。
        "redispatch-queued": {
            "task": "app.worker.tasks.redispatch_queued",
            "schedule": 30.0,
        },
    },
)


@worker_process_init.connect
def _init_worker_process(**_kwargs) -> None:
    """prefork 子进程启动：在本进程建常驻事件循环、数据库引擎和 Redis 客户端。"""
    loop.init(settings)


@worker_process_shutdown.connect
def _shutdown_worker_process(**_kwargs) -> None:
    loop.shutdown()
