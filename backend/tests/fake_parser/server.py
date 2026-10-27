"""本地假解析服务（P3-09）：按解析服务 X01 的作业接口返回固定的统一解析结果。

只用于测试、联调和 P3-19 端到端，不进生产镜像。接口照 `parser/app/main.py`：

- ``GET /healthz``：存活检查（唯一不需要认证的接口）。
- ``POST /v1/jobs``：提交作业，返回 ``{id}``。
- ``GET /v1/jobs/{id}``：作业状态与进度，不存在返回 404。
- ``DELETE /v1/jobs/{id}``：取消作业。
- ``GET /v1/engines``：引擎列表。
- ``POST /v1/convert``：同步格式转换（X02 的真实实现在本服务器搁置）。

返回两套固定的 DoclingDocument 样例（``fixtures/pdf``、``fixtures/word``）：PDF 带
页码、坐标和表格；Word 带元素引用（``#/texts/12`` 等）且没有页码。导出
``doc.json``、``doc.md``，图片写到 ``images/``，落到请求的 ``output_prefix`` /
``images_prefix``（对象存储用与主服务同一套 S3 配置）。

行为由请求参数控制，便于测试重试、取消、预览转换失败：

- ``engine_params.pages``：页数（默认取样例页数），用于按页报进度。
- ``engine_params.sleep_s``：每页报进度前睡眠的秒数。
- ``engine_params.error_code`` / ``error_message``：报完进度后返回该错误码。
- ``engine_params.temporary_fail_times``：前 N 次提交都返回 ``error_code``，之后成功。
- ``engine_params.missing_times``：前 N 次提交接受后立即丢弃（模拟解析服务重启，
  查询返回 404）。
- ``POST /v1/convert`` 请求里带 ``error_code`` 时返回 422 ``CONVERT_FAILED``；测试
  也可以直接设置 ``app.state.fail_convert_targets``。

启动（默认 59010，可用 ``FAKE_PARSER_PORT`` 覆盖）：

    make fake-parser
    FAKE_PARSER_PORT=19010 make fake-parser
    cd backend && FAKE_PARSER_PORT=19010 uv run python -m tests.fake_parser.server
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.errors import StorageError
from app.core.storage import Storage

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# 解析服务的错误码中文说明（与 parser/app/errors.py 保持一致）。
ERROR_MESSAGES = {
    "UNSUPPORTED_TYPE": "不支持的文件类型",
    "ENGINE_UNAVAILABLE": "解析引擎不可用",
    "FILE_CORRUPTED": "文件已损坏，无法解析",
    "FILE_ENCRYPTED": "文件已加密，无法解析",
    "FILE_TYPE_MISMATCH": "文件内容与扩展名不一致",
    "FILE_TOO_LARGE": "文件超过解析引擎的大小上限",
    "ARCHIVE_TOO_LARGE": "压缩包解压后过大，已拒绝解析",
    "TOO_MANY_PAGES": "文档页数超过上限",
    "TIMEOUT": "解析超时，请稍后重试或换用更小的文档",
    "CANCELLED": "作业已取消",
    "REMOTE_ERROR": "远程解析服务调用失败",
    "ENGINE_ERROR": "解析引擎执行失败",
    "CONVERT_FAILED": "文件转换失败",
}


def _message(code: str, override: str | None = None) -> str:
    return override or ERROR_MESSAGES.get(code, "解析失败")


@dataclass
class FakeJob:
    """内存里的假作业（知识库技术方案 6.1：作业只在内存）。"""

    id: str
    payload: dict[str, Any]
    status: str = "queued"
    done_pages: int = 0
    total_pages: int = 0
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


@dataclass
class Sample:
    """一套固定的统一解析结果。"""

    name: str
    pages: int
    ocr_pages: int
    engine_version: str
    doc_json: bytes
    doc_md: bytes
    images: dict[str, bytes]


_SAMPLES: dict[str, Sample] = {}


def _load_sample(name: str) -> Sample:
    if name in _SAMPLES:
        return _SAMPLES[name]
    directory = FIXTURES_DIR / name
    images = (
        {path.name: path.read_bytes() for path in sorted((directory / "images").glob("*.png"))}
        if (directory / "images").is_dir()
        else {}
    )
    sample = Sample(
        name=name,
        pages=2 if name == "pdf" else 0,
        ocr_pages=0,
        engine_version="docling-fake-1.0",
        doc_json=(directory / "doc.json").read_bytes(),
        doc_md=(directory / "doc.md").read_bytes(),
        images=images,
    )
    _SAMPLES[name] = sample
    return sample


def _sample_for(file_type: str) -> Sample:
    """按文件类型返回样例：PDF、Word 各一套，其余用 PDF 样例。"""
    if file_type == "word":
        return _load_sample("word")
    return _load_sample("pdf")


class FakeParserError(Exception):
    """返回标准错误响应（与解析服务一致）。"""

    def __init__(self, status_code: int, code: str, message: str | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = _message(code, message)
        super().__init__(self.message)


def create_app(settings: Settings | None = None) -> FastAPI:
    """建假解析服务应用。测试可以直接传 `Settings`，与主服务共用对象存储。"""
    resolved = settings or get_settings()
    app = FastAPI(title="ADP 假解析服务")

    app.state.settings = resolved
    app.state.storage = Storage(resolved)
    # 内存状态：测试可以直接读 / 改这些字段。
    app.state.jobs: dict[str, FakeJob] = {}
    app.state.submissions: list[dict[str, Any]] = []
    app.state.attempts: dict[str, int] = {}
    app.state.cancelled: list[str] = []
    app.state.fail_convert_targets: set[str] = set()

    @app.exception_handler(FakeParserError)
    async def _handle_error(_request: Request, exc: FakeParserError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": None},
        )

    async def require_token(
        request: Request, authorization: str | None = Header(default=None)
    ) -> None:
        expected = f"Bearer {request.app.state.settings.PARSER_TOKEN}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise FakeParserError(401, "UNAUTHORIZED", "认证失败")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/jobs", status_code=202, dependencies=[Depends(require_token)])
    async def create_job(payload: dict[str, Any], request: Request) -> dict[str, str]:
        state = request.app.state
        source_key = str(payload.get("source_key") or "")
        attempt = state.attempts.get(source_key, 0) + 1
        state.attempts[source_key] = attempt
        state.submissions.append(payload)

        job = FakeJob(id=uuid.uuid4().hex, payload=payload)
        params = payload.get("engine_params") or {}
        missing_times = int(params.get("missing_times") or 0)
        if attempt <= missing_times:
            # 模拟解析服务重启：作业接受后丢失，查询返回 404（6.1）。
            return {"id": job.id}

        state.jobs[job.id] = job
        job.task = asyncio.create_task(_run_job(state, job))
        return {"id": job.id}

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
    async def get_job(job_id: str, request: Request) -> dict[str, Any]:
        job = request.app.state.jobs.get(job_id)
        if job is None:
            raise FakeParserError(404, "JOB_NOT_FOUND", "作业不存在")
        return _job_status(job)

    @app.delete("/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
    async def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
        state = request.app.state
        job = state.jobs.get(job_id)
        if job is None:
            raise FakeParserError(404, "JOB_NOT_FOUND", "作业不存在")
        if job.status in ("queued", "running"):
            job.status = "cancelled"
            job.error = {"code": "CANCELLED", "message": _message("CANCELLED")}
            if job.task is not None:
                job.task.cancel()
        state.cancelled.append(job_id)
        return _job_status(job)

    @app.get("/v1/engines", dependencies=[Depends(require_token)])
    async def list_engines() -> list[dict[str, Any]]:
        return [
            {
                "name": "docling",
                "file_types": ["pdf", "word", "ppt", "image"],
                "available": True,
                "reason": None,
            },
            {
                "name": "pypdfium",
                "file_types": ["pdf"],
                "available": True,
                "reason": None,
            },
            {
                "name": "markitdown",
                "file_types": ["word", "ppt"],
                "available": True,
                "reason": None,
            },
            {
                "name": "builtin",
                "file_types": ["excel", "csv", "markdown", "txt", "html"],
                "available": True,
                "reason": None,
            },
            {
                "name": "mineru",
                "file_types": ["pdf", "image", "word", "ppt", "html", "epub", "ofd"],
                "available": False,
                "reason": "未配置",
            },
            {
                "name": "paddleocr",
                "file_types": ["pdf", "image"],
                "available": False,
                "reason": "未配置",
            },
        ]

    @app.post("/v1/convert", dependencies=[Depends(require_token)])
    async def convert(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        state = request.app.state
        source_key = str(payload.get("source_key") or "")
        output_key = str(payload.get("output_key") or "")
        target = str(payload.get("target") or "")
        if payload.get("error_code") or target in state.fail_convert_targets:
            raise FakeParserError(422, "CONVERT_FAILED", "文件转换失败")
        try:
            data = await state.storage.get_bytes(source_key)
        except StorageError as exc:
            raise FakeParserError(422, "CONVERT_FAILED", "文件转换失败") from exc
        await state.storage.put_bytes(output_key, data)
        return {"output_key": output_key, "size": len(data), "duration_ms": 5}

    return app


def _job_status(job: FakeJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "progress": {"done_pages": job.done_pages, "total_pages": job.total_pages},
        "result": job.result,
        "error": job.error,
    }


async def _run_job(state: Any, job: FakeJob) -> None:
    """后台执行作业：按页报进度，按参数返回错误或写出固定解析结果。"""
    job.status = "running"
    params = job.payload.get("engine_params") or {}
    sample = _sample_for(str(job.payload.get("file_type") or "pdf"))
    pages = int(params.get("pages") or sample.pages)
    sleep_s = float(params.get("sleep_s") or 0)
    job.total_pages = pages
    for page in range(1, pages + 1):
        await asyncio.sleep(sleep_s)
        if job.status == "cancelled":
            return
        job.done_pages = page

    error_code = params.get("error_code")
    source_key = str(job.payload.get("source_key") or "")
    attempt = state.attempts.get(source_key, 1)
    temporary_fail_times = int(params.get("temporary_fail_times") or 0)
    if error_code and (temporary_fail_times == 0 or attempt <= temporary_fail_times):
        job.error = {
            "code": str(error_code),
            "message": _message(str(error_code), params.get("error_message")),
        }
        job.status = "failed"
        return

    await _store_result(state, job, sample)
    job.status = "succeeded"


async def _store_result(state: Any, job: FakeJob, sample: Sample) -> None:
    """把固定样例写到请求的 output_prefix / images_prefix。"""
    output_prefix = str(job.payload.get("output_prefix") or "").rstrip("/")
    images_prefix = str(job.payload.get("images_prefix") or "").rstrip("/")
    await state.storage.put_bytes(f"{output_prefix}/doc.json", sample.doc_json)
    await state.storage.put_bytes(f"{output_prefix}/doc.md", sample.doc_md)
    for name, data in sample.images.items():
        await state.storage.put_bytes(f"{images_prefix}/{name}", data)
    job.result = {
        "doc_key": f"{output_prefix}/doc.json",
        "md_key": f"{output_prefix}/doc.md",
        "images_prefix": job.payload.get("images_prefix"),
        "pages": sample.pages,
        "ocr_pages": sample.ocr_pages,
        "empty_pages": [],
        "engine": str(job.payload.get("engine") or "docling"),
        "engine_version": sample.engine_version,
        "duration_ms": 12,
    }
    job.total_pages = sample.pages
    job.done_pages = sample.pages


def main() -> None:
    import uvicorn

    port = int(os.environ.get("FAKE_PARSER_PORT", "59010"))
    uvicorn.run(create_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
