from __future__ import annotations

import asyncio
import functools
import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

from adp_parser import SCHEMA_VERSION, SERVICE_NAME, __version__
from adp_parser.config import Settings
from adp_parser.dispatch import parse_request
from adp_parser.errors import ParserError
from adp_parser.extensions import list_ocr_providers
from adp_parser.validation import MIME_BY_EXTENSION, SUPPORTED_EXTENSIONS
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("adp.parser")

JSON_CONTENT_TYPES = {"", "application/json", "text/json", "application/problem+json"}


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fileName: str = Field(min_length=1, max_length=512)
    mime: str | None = Field(default=None, max_length=255)
    dataBase64: str | None = None
    text: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    executor = ThreadPoolExecutor(
        max_workers=settings.workers, thread_name_prefix="adp-parser"
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "adp-parser %s starting: token=%s workers=%s timeout=%.1fs formats=%s",
            __version__,
            "configured" if settings.token else "not configured",
            settings.workers,
            settings.timeout_seconds,
            ",".join(sorted(SUPPORTED_EXTENSIONS)),
        )
        try:
            yield
        finally:
            app.state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="ADP Parser Service",
        version=__version__,
        description=(
            "ADP 独立文档解析服务。只接受请求体中的内容，不读取客户端路径或 URL。"
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.executor = executor

    @app.exception_handler(ParserError)
    async def parser_error_handler(request: Request, exc: ParserError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_payload(),
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        messages = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", []))
            message = error.get("msg", "无效值")
            messages.append(f"{location}: {message}" if location else message)
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_request",
                "message": "请求体无效：" + "；".join(messages),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": f"http_{exc.status_code}", "message": detail},
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled parser request error: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "解析服务内部错误，请查看服务日志后重试。",
            },
        )

    async def require_token(request: Request) -> None:
        expected = request.app.state.settings.token
        if not expected:
            return
        supplied = request.headers.get("x-parser-token")
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise ParserError(
                "unauthorized",
                "缺少或错误的 X-Parser-Token。解析服务已配置 PARSER_TOKEN，"
                "所有 /v1 接口必须携带正确令牌。",
                401,
            )

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        current: Settings = request.app.state.settings
        return {
            "status": "ok",
            "version": __version__,
            "service": SERVICE_NAME,
            "schemaVersion": SCHEMA_VERSION,
            "authRequired": bool(current.token),
        }

    @app.get("/v1/engines", dependencies=[Depends(require_token)])
    async def engines(request: Request) -> dict[str, Any]:
        current: Settings = request.app.state.settings
        ocr_providers = list_ocr_providers()
        return {
            "schemaVersion": SCHEMA_VERSION,
            "service": SERVICE_NAME,
            "version": __version__,
            "engines": [
                {
                    "name": "pdfplumber",
                    "formats": ["pdf"],
                    "fallback": "pypdf",
                    "pageLocators": True,
                    "tables": "ruled tables via pdfplumber; plain text otherwise",
                    "headings": "heuristic font-size/bold inference",
                    "ocr": False,
                },
                {
                    "name": "python-docx",
                    "formats": ["docx"],
                    "pageLocators": False,
                    "paragraphLocators": True,
                    "tables": "structured table rows",
                    "headings": "Word heading styles/outline levels",
                    "ocr": False,
                },
                {
                    "name": "python-pptx",
                    "formats": ["pptx"],
                    "pageLocators": True,
                    "paragraphLocators": True,
                    "tables": "structured table rows",
                    "headings": "slide title",
                    "ocr": False,
                },
                {
                    "name": "openpyxl",
                    "formats": ["xlsx"],
                    "sheetLocators": True,
                    "rowLocators": True,
                    "tables": "first non-empty row treated as header",
                    "headings": "worksheet name",
                    "ocr": False,
                },
                {
                    "name": "beautifulsoup4",
                    "formats": ["html"],
                    "tables": "structured table rows",
                    "headings": "h1-h6",
                    "ocr": False,
                },
                {
                    "name": "markdown-lite",
                    "formats": ["md"],
                    "tables": "GFM pipe tables",
                    "headings": "ATX/setext headings",
                    "ocr": False,
                },
                {
                    "name": "python-csv",
                    "formats": ["csv"],
                    "rowLocators": True,
                    "tables": "first row treated as header",
                    "ocr": False,
                },
                {
                    "name": "plain-text",
                    "formats": ["txt"],
                    "paragraphLocators": True,
                    "headings": "not inferred",
                    "ocr": False,
                },
            ],
            "formats": [
                {
                    "extension": extension,
                    "mime": sorted(MIME_BY_EXTENSION[extension]),
                }
                for extension in sorted(SUPPORTED_EXTENSIONS)
            ],
            "ocr": {
                "available": bool(ocr_providers),
                "providers": ocr_providers,
                "detail": (
                    "未内置 OCR/视觉识别。扫描 PDF 无文本层时会明确失败；"
                    "可通过 adp_parser.extensions.register_ocr_provider 注册实现。"
                ),
            },
            "limits": {
                "maxInputBytes": current.limits.max_input_bytes,
                "maxTextChars": current.limits.max_text_chars,
                "maxElements": current.limits.max_elements,
                "maxPdfPages": current.limits.max_pdf_pages,
                "maxSlides": current.limits.max_slides,
                "maxExcelRows": current.limits.max_excel_rows,
                "maxExcelColumns": current.limits.max_excel_columns,
                "maxCsvRows": current.limits.max_csv_rows,
                "timeoutSeconds": current.timeout_seconds,
            },
        }

    @app.post("/v1/parse", dependencies=[Depends(require_token)])
    async def parse(request: Request) -> JSONResponse:
        current: Settings = request.app.state.settings
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in JSON_CONTENT_TYPES:
            raise ParserError(
                "invalid_content_type",
                "请求必须使用 application/json。",
                415,
            )

        body = await _read_json_body(request, current)
        try:
            parsed = ParseRequest.model_validate(body)
        except ValidationError as exc:
            raise ParserError(
                "invalid_request",
                "请求体无效：" + _format_validation_error(exc),
                422,
            ) from exc

        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(
            request.app.state.executor,
            functools.partial(
                parse_request,
                file_name=parsed.fileName,
                mime=parsed.mime,
                data_base64=parsed.dataBase64,
                text=parsed.text,
                settings=current,
            ),
        )
        try:
            result = await asyncio.wait_for(task, timeout=current.timeout_seconds)
        except TimeoutError as exc:
            logger.warning(
                "Parse timeout after %.1fs (file=%r)", current.timeout_seconds, parsed.fileName
            )
            raise ParserError(
                "parse_timeout",
                f"解析超时（{current.timeout_seconds:g} 秒）。文件过大或格式过于复杂；"
                "后台线程无法被强制中断，请重试或拆分文件。",
                504,
            ) from exc

        return JSONResponse(
            content=result.to_payload(), headers={"Cache-Control": "no-store"}
        )

    return app


async def _read_json_body(request: Request, settings: Settings) -> Any:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            declared = 0
        if declared > settings.request_max_bytes:
            raise ParserError(
                "payload_too_large",
                f"请求体超过 {settings.request_max_bytes // (1024 * 1024)}MB 上限。",
                413,
            )

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > settings.request_max_bytes:
            raise ParserError(
                "payload_too_large",
                f"请求体超过 {settings.request_max_bytes // (1024 * 1024)}MB 上限。",
                413,
            )
        chunks.append(chunk)
    if not chunks:
        raise ParserError("invalid_request", "请求体不能为空。", 400)

    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ParserError(
            "invalid_json", "请求体不是有效的 UTF-8 JSON。", 400
        ) from exc
    except json.JSONDecodeError as exc:
        raise ParserError(
            "invalid_json", f"请求体不是有效的 JSON（第 {exc.pos} 字符附近）。", 400
        ) from exc


def _format_validation_error(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        message = error.get("msg", "无效值")
        messages.append(f"{location}: {message}" if location else message)
    return "；".join(messages)


app = create_app()
