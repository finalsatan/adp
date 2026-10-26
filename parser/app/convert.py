"""常驻 unoserver 的生命周期、串行转换和对象存储接口。"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .errors import AppError, ParseError
from .filecheck import check_file
from .filetypes import normalize_ext
from .storage import Storage

CONVERT_TIMEOUT_S = 120
CONVERSIONS = {
    "docx": {"doc", "rtf", "odt"},
    "pptx": {"ppt", "odp"},
    "xlsx": {"xls", "ods"},
    "pdf": {"doc", "docx", "rtf", "odt", "ppt", "pptx", "odp"},
}


class ConvertRequest(BaseModel):
    source_key: str = Field(min_length=1)
    source_ext: str = Field(min_length=1)
    target: Literal["docx", "pptx", "xlsx", "pdf"]
    output_key: str = Field(min_length=1)


class ConvertResult(BaseModel):
    output_key: str
    size: int
    duration_ms: int


def _failed(message: str = "文件转换失败，请检查文件是否损坏后重试") -> AppError:
    return AppError(422, "CONVERT_FAILED", message)


class Converter:
    """一个服务进程对应一个 LibreOffice 实例；锁同时保护转换和重启。"""

    def __init__(self, *, timeout_s: float = CONVERT_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._profile: tempfile.TemporaryDirectory | None = None
        self.starts = 0

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    def start(self) -> None:
        if self._monitor is not None:
            return
        self._stop.clear()
        with self._lock:
            self._ensure_process()
        self._monitor = threading.Thread(target=self._watch, name="unoserver-watch", daemon=True)
        self._monitor.start()

    def stop(self) -> None:
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=self.timeout_s + 5)
            self._monitor = None
        with self._lock:
            self._kill_process()

    def _watch(self) -> None:
        while not self._stop.wait(0.5):
            with self._lock:
                if not self._stop.is_set():
                    self._ensure_process()

    def _ensure_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._kill_process()
        self._profile = tempfile.TemporaryDirectory(prefix="parser-lo-")
        # Debian 的 python3-uno 绑定系统 Python；客户端继续用应用的 Python 3.12。
        try:
            self._process = subprocess.Popen(
                [
                    "/opt/unoserver/bin/python",
                    "-m",
                    "unoserver.server",
                    "--interface",
                    "127.0.0.1",
                    "--uno-interface",
                    "127.0.0.1",
                    "--port",
                    "2003",
                    "--uno-port",
                    "2002",
                    "--user-installation",
                    self._profile.name,
                    "--conversion-timeout",
                    str(CONVERT_TIMEOUT_S),
                    "--quiet",
                ],
                stdout=subprocess.DEVNULL,
                stderr=None,
                start_new_session=True,
            )
            self.starts += 1
        except OSError:
            # Mac 上的纯 Python 测试不要求安装 LibreOffice；转换请求如实报失败。
            self._kill_process()

    def _kill_process(self) -> None:
        if self._process is not None:
            # 整组回收，防止超时后遗留 soffice 子进程或占用监听端口。
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._process.pid, signal.SIGKILL)
            self._process.wait()
            self._process = None
        if self._profile is not None:
            self._profile.cleanup()
            self._profile = None

    def convert_file(self, source: Path, target: Path, *, deadline: float | None = None) -> None:
        """供转换接口和后续引擎调用；120 秒预算包含排队、启动和转换。"""
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout_s
        if not self._lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise _failed("文件转换等待超时，请稍后重试")
        try:
            self._ensure_process()
            if self._process is None:
                raise _failed("格式转换服务不可用，请联系管理员")
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired("unoserver", self.timeout_s)
                if self._process.poll() is not None:
                    raise _failed()
                try:
                    with socket.create_connection(("127.0.0.1", 2003), timeout=min(remaining, 0.2)):
                        break
                except OSError:
                    time.sleep(min(remaining, 0.1))
            subprocess.run(
                [
                    str(Path(sys.executable).with_name("unoconvert")),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "2003",
                    "--host-location",
                    "local",
                    "--quiet",
                    str(source.resolve()),
                    str(target.resolve()),
                ],
                check=True,
                timeout=max(0, deadline - time.monotonic()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if not target.is_file() or target.stat().st_size == 0:
                raise _failed()
        except subprocess.TimeoutExpired as exc:
            self._kill_process()
            self._ensure_process()
            raise _failed("文件转换超时，请换用更小的文件后重试") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise _failed() from exc
        finally:
            self._lock.release()

    def convert(self, payload: ConvertRequest, storage: Storage) -> ConvertResult:
        started = time.monotonic()
        ext = normalize_ext(payload.source_ext)
        if ext not in CONVERSIONS[payload.target]:
            raise _failed("不支持此源格式与目标格式的转换")
        try:
            with tempfile.TemporaryDirectory(prefix="parser-convert-") as work_dir:
                source = Path(work_dir) / f"source.{ext}"
                output = Path(work_dir) / f"output.{payload.target}"
                storage.download(payload.source_key, source)
                check_file(source, ext)
                self.convert_file(source, output, deadline=started + self.timeout_s)
                check_file(output, payload.target)
                size = output.stat().st_size
                storage.upload_file(output, payload.output_key)
            return ConvertResult(
                output_key=payload.output_key,
                size=size,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except AppError:
            raise
        except ParseError as exc:
            raise _failed(exc.message) from exc
        except Exception as exc:
            # 不把对象存储连接信息、服务内部路径等写进用户错误。
            raise _failed() from exc
