"""内存中的作业存储（知识库技术方案 6.1：作业状态只在内存，重启即丢）。"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .engines.base import RemoteConfig

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"


class QueueFull(Exception):
    """排队作业数达到 PARSER_QUEUE_MAX。"""


@dataclass
class TaskSpec:
    """提交作业时定下来的、可被 pickl 到子进程的参数。"""

    source_key: str
    file_type: str
    ext: str
    engine: str
    engine_params: dict
    preprocess: dict
    table: dict
    output_prefix: str
    images_prefix: str
    remote: RemoteConfig | None = None


@dataclass
class Job:
    id: str
    status: str
    task: TaskSpec
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    worker_id: int | None = None
    done_pages: int = 0
    total_pages: int = 0
    result: dict | None = None
    error: dict | None = None
    work_dir: str | None = None


class JobStore:
    def __init__(self, queue_max: int) -> None:
        self._queue_max = queue_max
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def create(self, task: TaskSpec) -> Job:
        with self._lock:
            queued = sum(1 for job in self._jobs.values() if job.status == QUEUED)
            if queued >= self._queue_max:
                raise QueueFull()
            job = Job(
                id=uuid.uuid4().hex,
                status=QUEUED,
                task=task,
                created_at=time.time(),
            )
            self._jobs[job.id] = job
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def claim_next_queued(
        self, worker_id: int, predicate: Callable[[Job], bool] = lambda job: True
    ) -> Job | None:
        """把最早排队的作业置为 running 并返回；没有就返回 None。"""
        with self._lock:
            queued = sorted(
                (job for job in self._jobs.values() if job.status == QUEUED and predicate(job)),
                key=lambda job: job.created_at,
            )
            if not queued:
                return None
            job = queued[0]
            job.status = RUNNING
            job.worker_id = worker_id
            job.started_at = time.time()
            return job

    def running(self) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.status == RUNNING]

    def update_progress(self, job_id: str, done_pages: int, total_pages: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status == RUNNING:
                job.done_pages = done_pages
                job.total_pages = total_pages

    def finish(self, job_id: str, result: dict) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status == RUNNING:
                job.status = SUCCEEDED
                job.result = result
                job.finished_at = time.time()
                pages = result.get("pages")
                if pages is not None:
                    job.total_pages = pages
                    job.done_pages = pages

    def fail(self, job_id: str, code: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status == RUNNING:
                job.status = FAILED
                job.error = {"code": code, "message": message}
                job.finished_at = time.time()

    def cancel(self, job_id: str) -> Job | None:
        from .errors import message_for

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in (QUEUED, RUNNING):
                job.status = CANCELLED
                job.error = {"code": "CANCELLED", "message": message_for("CANCELLED")}
                job.finished_at = time.time()
            return job
