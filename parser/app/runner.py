"""作业执行：主进程负责对象存储读写和临时目录，子进程只跑引擎。"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .engines.base import ParseError, ParseOutput
from .filecheck import check_file
from .jobs import CANCELLED, Job, JobStore, TaskSpec
from .pool import WorkerPool
from .postprocess import postprocess
from .storage import Storage


class Runner:
    """作业入口：提交、查询、取消，并实现 WorkerPool 的 TaskExecutor。"""

    def __init__(self, settings: Settings, store: JobStore, storage: Storage) -> None:
        self._settings = settings
        self._store = store
        self._storage = storage
        self._pool = WorkerPool(settings, store, self)

    def start(self) -> None:
        self._pool.start()

    def stop(self) -> None:
        self._pool.stop()

    def alive_workers(self) -> int:
        return self._pool.alive_workers()

    def submit(self, task: TaskSpec) -> Job:
        job = self._store.create(task)
        self._pool.wake()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._store.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self._store.cancel(job_id)
        if job is None:
            return None
        if job.status == CANCELLED and job.worker_id is not None:
            self._pool._kill_job(job_id)
        return job

    # ---- WorkerPool.TaskExecutor ----
    def prepare(self, job: Job) -> dict[str, Any]:
        task = job.task
        work_dir = tempfile.mkdtemp(prefix=f"parser-{job.id}-")
        job.work_dir = work_dir
        ext = task.ext.lstrip(".")
        input_path = Path(work_dir) / f"input.{ext}"
        try:
            self._storage.download(task.source_key, input_path)
        except Exception as exc:  # noqa: BLE001
            raise ParseError("FILE_CORRUPTED", f"源文件下载失败：{exc}") from exc
        check_file(input_path, ext)
        return {
            "input_path": str(input_path),
            "file_type": task.file_type,
            "ext": ext,
            "engine": task.engine,
            "engine_params": task.engine_params,
            "preprocess": task.preprocess,
            "table": task.table,
            "work_dir": work_dir,
            "remote": task.remote,
        }

    def finish(self, job: Job, output: ParseOutput) -> dict[str, Any]:
        if job.work_dir is None:
            raise ParseError("ENGINE_ERROR", "作业临时目录丢失")
        if output.pages > self._settings.parser_max_pages:
            raise ParseError(
                "TOO_MANY_PAGES",
                f"文档页数 {output.pages} 超过上限 {self._settings.parser_max_pages} 页",
            )

        processed = postprocess(
            output,
            options=job.task.preprocess,
            work_dir=job.work_dir,
            images_prefix=job.task.images_prefix,
            storage=self._storage,
        )
        output = processed.output

        out_dir = Path(job.work_dir) / "out"
        prefix = job.task.output_prefix.rstrip("/")
        doc_key = f"{prefix}/doc.json"
        md_key = f"{prefix}/doc.md"
        self._storage.upload_file(out_dir / "doc.json", doc_key)
        self._storage.upload_file(out_dir / "doc.md", md_key)

        duration_ms = int((time.time() - (job.started_at or job.created_at)) * 1000)
        return {
            "doc_key": doc_key,
            "md_key": md_key,
            "images_prefix": job.task.images_prefix,
            "pages": output.pages,
            "ocr_pages": output.ocr_pages,
            "empty_pages": output.empty_pages,
            "images": processed.images,
            "engine": job.task.engine,
            "engine_version": output.engine_version,
            "duration_ms": duration_ms,
        }

    def cleanup(self, job: Job) -> None:
        if job.work_dir:
            shutil.rmtree(job.work_dir, ignore_errors=True)
            job.work_dir = None
