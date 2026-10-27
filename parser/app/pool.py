"""常驻子进程池。

自己管理 PARSER_WORKERS 个 spawn 方式的常驻子进程，每个子进程启动时加载一次
引擎所需的模型；主进程通过队列下发任务、接收进度和结果。

超时或取消时，普通进程池无法中断正在执行的任务，所以这里直接结束执行该作业的
子进程，并补起一个新的，保证子进程数始终为 PARSER_WORKERS。
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing as mp
import os
import threading
import time
from typing import Any, Protocol

from .config import Settings
from .engines.base import ParseContext, ParseError, ParseOutput
from .engines.registry import bootstrap, get_engine, list_engines
from .errors import message_for
from .jobs import RUNNING, Job, JobStore

logger = logging.getLogger(__name__)

# 主进程与子进程之间的消息类型
_MSG_READY = "ready"
_MSG_PROGRESS = "progress"
_MSG_DONE = "done"
_MSG_ERROR = "error"


class TaskExecutor(Protocol):
    """作业在主进程侧的准备、收尾和清理，由 runner 实现。"""

    def prepare(self, job: Job) -> dict[str, Any]: ...

    def finish(self, job: Job, output: ParseOutput) -> dict[str, Any]: ...

    def cleanup(self, job: Job) -> None: ...


def _worker_main(
    worker_id: int,
    task_queue: Any,
    result_queue: Any,
    enable_fake_engine: bool,
) -> None:
    """子进程主循环，必须在模块顶层，才能被 spawn 方式启动。"""
    bootstrap(enable_fake_engine)
    # 引擎自报的模型加载（X06 的 Docling）：加载完成后再报到，主进程只把任务发给已就绪的子进程。
    # 加载失败不阻止报到，作业执行时会返回具体错误；否则这个子进程会一直不就绪。
    for engine in list_engines():
        warmup = getattr(engine, "warmup", None)
        if warmup is None:
            continue
        try:
            warmup()
        except Exception:  # noqa: BLE001 - 预热失败只记日志，不拖垮子进程
            logger.exception("引擎 %s 预热失败", engine.name)
    result_queue.put({"type": _MSG_READY, "worker_id": worker_id, "pid": os.getpid()})
    while True:
        task = task_queue.get()
        if task is None:
            break
        job_id = task["job_id"]
        try:
            engine = get_engine(task["engine"])
            if engine is None:
                raise ParseError("ENGINE_UNAVAILABLE", "解析引擎不存在")

            def report(done_pages: int, total_pages: int, _job_id: str = job_id) -> None:
                result_queue.put(
                    {
                        "type": _MSG_PROGRESS,
                        "job_id": _job_id,
                        "worker_id": worker_id,
                        "done_pages": done_pages,
                        "total_pages": total_pages,
                    }
                )

            ctx = ParseContext(
                input_path=task["input_path"],
                file_type=task["file_type"],
                ext=task["ext"],
                engine_params=task["engine_params"],
                preprocess=task["preprocess"],
                table=task["table"],
                work_dir=task["work_dir"],
                report=report,
                remote=task["remote"],
            )
            output = engine.parse(ctx)
            result_queue.put(
                {
                    "type": _MSG_DONE,
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "output": output,
                }
            )
        except ParseError as exc:
            result_queue.put(
                {
                    "type": _MSG_ERROR,
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "code": exc.code,
                    "message": exc.message,
                }
            )
        except BaseException as exc:  # noqa: BLE001 - 兜底，避免子进程静默退出
            result_queue.put(
                {
                    "type": _MSG_ERROR,
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "code": "ENGINE_ERROR",
                    "message": f"解析引擎执行失败：{exc}",
                }
            )


class _Slot:
    def __init__(self, index: int, process: Any, queue: Any) -> None:
        self.index = index
        self.process = process
        self.queue = queue
        self.job_id: str | None = None
        self.ready = False


class WorkerPool:
    def __init__(self, settings: Settings, store: JobStore, executor: TaskExecutor) -> None:
        self._settings = settings
        self._store = store
        self._executor = executor
        self._timeout_s = settings.parser_job_timeout_s
        self._ctx = mp.get_context("spawn")
        self._result_queue: Any = None
        self._slots: list[_Slot] = []
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []
        self._remote: dict[str, threading.Event] = {}

    # ---- 生命周期 ----
    def start(self) -> None:
        bootstrap(self._settings.parser_enable_fake_engine)
        self._result_queue = self._ctx.Queue()
        for index in range(self._settings.parser_workers):
            self._slots.append(self._spawn(index))
        for target in (self._read_results, self._dispatch_loop, self._monitor_loop):
            thread = threading.Thread(target=target, name=target.__name__, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            for event in self._remote.values():
                event.set()
        self._wake.set()
        if self._result_queue is not None:
            with contextlib.suppress(Exception):
                self._result_queue.put(None)
        with self._lock:
            slots = list(self._slots)
        for slot in slots:
            process = slot.process
            if process.is_alive():
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
            with contextlib.suppress(Exception):
                slot.queue.close()
        if self._result_queue is not None:
            with contextlib.suppress(Exception):
                self._result_queue.close()
        for thread in self._threads:
            thread.join(timeout=1)

    def alive_workers(self) -> int:
        with self._lock:
            return sum(1 for slot in self._slots if slot.process.is_alive())

    def wake(self) -> None:
        self._wake.set()

    # ---- 子进程 ----
    def _spawn(self, index: int) -> _Slot:
        queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=_worker_main,
            args=(index, queue, self._result_queue, self._settings.parser_enable_fake_engine),
            name=f"parser-worker-{index}",
            daemon=True,
        )
        process.start()
        return _Slot(index, process, queue)

    def _replace(self, index: int) -> None:
        """结束 index 号子进程并补一个新的。"""
        with self._lock:
            old = self._slots[index]
            old.job_id = None
            old.ready = False
            process = old.process
        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
        with contextlib.suppress(Exception):
            old.queue.close()
        new_slot = self._spawn(index)
        with self._lock:
            self._slots[index] = new_slot

    def _slot_by_job(self, job_id: str) -> _Slot | None:
        for slot in self._slots:
            if slot.job_id == job_id:
                return slot
        return None

    def _release(self, slot: _Slot, job_id: str) -> None:
        with self._lock:
            if slot.job_id == job_id:
                slot.job_id = None

    def _kill_job(self, job_id: str) -> None:
        """结束正在执行 job_id 的子进程并补齐；作业状态由调用方负责。"""
        with self._lock:
            event = self._remote.get(job_id)
            if event is not None:
                event.set()
                return
            slot = self._slot_by_job(job_id)
            if slot is None:
                return
            slot.job_id = None
            slot.ready = False
            index = slot.index
        self._replace(index)
        job = self._store.get(job_id)
        if job is not None:
            self._executor.cleanup(job)
        self._wake.set()

    # ---- 调度 ----
    def _dispatch_loop(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(0.05)
            self._wake.clear()
            self._dispatch()

    @staticmethod
    def _is_remote(job: Job) -> bool:
        engine = get_engine(job.task.engine)
        return bool(getattr(engine, "remote", False))

    def _dispatch_remote(self) -> None:
        while True:
            with self._lock:
                if len(self._remote) >= max(1, self._settings.parser_remote_concurrency):
                    return
                job = self._store.claim_next_queued(-1, self._is_remote)
                if job is None:
                    return
                event = threading.Event()
                self._remote[job.id] = event
            threading.Thread(target=self._run_remote, args=(job, event), daemon=True).start()

    def _run_remote(self, job: Job, event: threading.Event) -> None:
        try:
            payload = self._executor.prepare(job)
            if event.is_set():
                return
            engine = get_engine(job.task.engine)
            if engine is None:
                raise ParseError("ENGINE_UNAVAILABLE", "解析引擎不存在")
            ctx = ParseContext(
                input_path=payload["input_path"],
                file_type=payload["file_type"],
                ext=payload["ext"],
                engine_params=payload["engine_params"],
                preprocess=payload["preprocess"],
                table=payload["table"],
                work_dir=payload["work_dir"],
                remote=payload["remote"],
                report=lambda done, total: self._store.update_progress(job.id, done, total),
                cancel_event=event,
            )
            output = engine.parse(ctx)
            if not event.is_set() and job.status == RUNNING:
                result = self._executor.finish(job, output)
                self._store.finish(job.id, result)
        except ParseError as exc:
            self._store.fail(job.id, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            self._store.fail(job.id, "ENGINE_ERROR", f"解析引擎执行失败：{exc}")
        finally:
            self._executor.cleanup(job)
            with self._lock:
                self._remote.pop(job.id, None)
            self._wake.set()

    def _dispatch(self) -> None:
        self._dispatch_remote()
        while True:
            with self._lock:
                slot = next(
                    (
                        s
                        for s in self._slots
                        if s.job_id is None and s.ready and s.process.is_alive()
                    ),
                    None,
                )
                if slot is None:
                    return
                job = self._store.claim_next_queued(
                    slot.index, lambda job: not self._is_remote(job)
                )
                if job is None:
                    return
                slot.job_id = job.id

            try:
                payload = self._executor.prepare(job)
            except ParseError as exc:
                self._release(slot, job.id)
                self._store.fail(job.id, exc.code, exc.message)
                self._executor.cleanup(job)
                continue
            except Exception as exc:  # noqa: BLE001
                self._release(slot, job.id)
                self._store.fail(job.id, "ENGINE_ERROR", f"作业准备失败：{exc}")
                self._executor.cleanup(job)
                continue

            payload["job_id"] = job.id
            try:
                slot.queue.put(payload)
            except Exception as exc:  # noqa: BLE001
                self._release(slot, job.id)
                self._store.fail(job.id, "ENGINE_ERROR", f"无法提交到解析进程：{exc}")
                self._executor.cleanup(job)

    # ---- 结果读取 ----
    def _read_results(self) -> None:
        while not self._stopping.is_set():
            try:
                message = self._result_queue.get()
            except Exception:  # noqa: BLE001 - 队列关闭或进程退出时结束读取线程
                return
            if message is None:
                return
            try:
                self._handle(message)
            except Exception:  # noqa: BLE001 - 读取线程不能因单条消息退出
                continue

    def _handle(self, message: dict) -> None:
        if message.get("type") == _MSG_READY:
            with self._lock:
                slot = next(
                    (
                        s
                        for s in self._slots
                        if s.index == message.get("worker_id")
                        and s.process.pid == message.get("pid")
                    ),
                    None,
                )
                if slot is not None:
                    slot.ready = True
            self._wake.set()
            return

        job_id = message.get("job_id")
        job = self._store.get(job_id)
        if job is None:
            return

        kind = message.get("type")
        if kind == _MSG_PROGRESS:
            self._store.update_progress(job_id, message["done_pages"], message["total_pages"])
            return

        slot = self._slot_by_job(job_id)
        try:
            if kind == _MSG_DONE:
                if job.status == RUNNING:
                    try:
                        result = self._executor.finish(job, message["output"])
                        self._store.finish(job_id, result)
                    except ParseError as exc:
                        self._store.fail(job_id, exc.code, exc.message)
                    except Exception as exc:  # noqa: BLE001
                        self._store.fail(job_id, "ENGINE_ERROR", f"解析结果处理失败：{exc}")
            elif kind == _MSG_ERROR:
                self._store.fail(
                    job_id,
                    message.get("code", "ENGINE_ERROR"),
                    message.get("message") or message_for("ENGINE_ERROR"),
                )
        finally:
            self._executor.cleanup(job)
            if slot is not None:
                self._release(slot, job_id)
            self._wake.set()

    # ---- 超时 ----
    def _monitor_loop(self) -> None:
        while not self._stopping.is_set():
            time.sleep(0.2)
            now = time.time()
            for job in self._store.running():
                if job.started_at and now - job.started_at > self._timeout_s:
                    self._store.fail(job.id, "TIMEOUT", message_for("TIMEOUT"))
                    self._kill_job(job.id)
