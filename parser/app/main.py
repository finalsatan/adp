"""解析服务入口：创建应用、注册路由。"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import require_token
from .config import Settings, get_settings
from .convert import Converter, ConvertRequest, ConvertResult
from .engines.base import RemoteConfig
from .engines.registry import bootstrap, get_engine, list_engines
from .errors import AppError, message_for
from .filetypes import FILE_TYPES, is_known_type, normalize_ext
from .jobs import Job, JobStore, QueueFull, TaskSpec
from .runner import Runner
from .schemas import JobCreate, JobStatus
from .storage import Storage


def _runner(request: Request) -> Runner:
    return request.app.state.runner


def _job_status(job: Job) -> JobStatus:
    return JobStatus(
        id=job.id,
        status=job.status,
        progress={"done_pages": job.done_pages, "total_pages": job.total_pages},
        result=job.result,
        error=job.error,
    )


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str | None = None,
    detail: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message or message_for(code),
            "detail": detail,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bootstrap(settings.parser_enable_fake_engine)
        storage = Storage(settings)
        store = JobStore(settings.parser_queue_max)
        runner = Runner(settings, store, storage)
        converter = Converter()
        runner.start()
        converter.start()
        app.state.settings = settings
        app.state.runner = runner
        app.state.converter = converter
        app.state.storage = storage
        try:
            yield
        finally:
            converter.stop()
            runner.stop()

    app = FastAPI(title="ADP 解析服务", lifespan=lifespan)

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(request, exc.status_code, exc.code, exc.message, exc.detail)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/convert", dependencies=[Depends(require_token)])
    def convert_file(payload: ConvertRequest, request: Request) -> ConvertResult:
        return request.app.state.converter.convert(payload, request.app.state.storage)

    @app.post("/v1/jobs", status_code=202, dependencies=[Depends(require_token)])
    async def create_job(payload: JobCreate, request: Request) -> dict[str, str]:
        if not is_known_type(payload.file_type):
            raise AppError(400, "UNSUPPORTED_TYPE", f"未知的文件类型：{payload.file_type}")
        ext = normalize_ext(payload.ext)

        engine = get_engine(payload.engine)
        if engine is None:
            raise AppError(400, "ENGINE_UNAVAILABLE", f"解析引擎不存在：{payload.engine}")
        available, reason = engine.availability()
        if not available:
            raise AppError(400, "ENGINE_UNAVAILABLE", reason or message_for("ENGINE_UNAVAILABLE"))
        if not engine.supports(payload.file_type, ext):
            raise AppError(
                400,
                "UNSUPPORTED_TYPE",
                f"引擎 {payload.engine} 不支持 {payload.file_type} 类型",
            )

        remote = None
        if payload.remote is not None:
            remote = RemoteConfig(
                base_url=payload.remote.base_url,
                api_key=payload.remote.api_key,
            )
        task = TaskSpec(
            source_key=payload.source_key,
            file_type=payload.file_type,
            ext=ext,
            engine=payload.engine,
            engine_params=payload.engine_params,
            preprocess=payload.preprocess.model_dump(),
            table=payload.table.model_dump(),
            output_prefix=payload.output_prefix,
            images_prefix=payload.images_prefix,
            remote=remote,
        )
        try:
            job = _runner(request).submit(task)
        except QueueFull as exc:
            raise AppError(429, "QUEUE_FULL") from exc
        return {"id": job.id}

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
    async def get_job(job_id: str, request: Request) -> JobStatus:
        job = _runner(request).get(job_id)
        if job is None:
            raise AppError(404, "JOB_NOT_FOUND")
        return _job_status(job)

    @app.delete("/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
    async def cancel_job(job_id: str, request: Request) -> JobStatus:
        runner = _runner(request)
        if runner.get(job_id) is None:
            raise AppError(404, "JOB_NOT_FOUND")
        job = runner.cancel(job_id)
        if job is None:
            raise AppError(404, "JOB_NOT_FOUND")
        return _job_status(job)

    @app.get("/v1/engines", dependencies=[Depends(require_token)])
    async def list_engine_info() -> list[dict]:
        engines = []
        for engine in list_engines():
            available, reason = engine.availability()
            file_types = [ft for ft, exts in FILE_TYPES.items() if engine.supports(ft, exts[0])]
            engines.append(
                {
                    "name": engine.name,
                    "file_types": file_types,
                    "available": available,
                    "reason": reason,
                }
            )
        return engines

    return app


app = create_app()
