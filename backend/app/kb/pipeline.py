"""入库流水线：步骤 1 转换、步骤 2 解析（P3-09，知识库技术方案 8.1、8.3）。

- 步骤 1 转换：选 Docling、MarkItDown、内置表格解析时，旧格式（doc、rtf、odt、
  ppt、odp、xls、ods）先调解析服务 `POST /v1/convert` 转成 docx / pptx / xlsx；
  Word、PPT 类另转一份预览 PDF，**预览失败只记录日志**（需求 4.1.8）。
- 步骤 2 解析：提交解析服务作业、轮询等待、写回进度与心跳；临时错误自动重试
  3 次，业务性错误立即失败；每步开始前检查 `run_id`，变化时取消解析作业并退出。
- 成功写 `document_parses`、`parse_records` 和 `documents` 的页数 / 状态 / 步骤。

分块（步骤 3）由 P3-10 接入，向量化与两代切换（步骤 5、6）由 P3-11 接入。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ParserUnavailableError, StorageError
from app.core.storage import (
    Storage,
    doc_file_key,
    images_prefix,
    kb_original_key,
)
from app.kb.parser_client import ParserClient, ParserJobTimeout
from app.kb.tables import Document, DocumentParse, IngestScheme, ParseRecord
from app.settings import service as settings_service
from app.worker.dispatch import RunCancelled, check_run_id
from app.worker.tokens import ParseConcurrencyLimited, ParseTokenLimiter, RetryLater

logger = logging.getLogger(__name__)

# 入库步骤序号（知识库技术方案 8.1）。
STEP_CONVERT = 1
STEP_PARSE = 2
# 解析完成后进入分块（步骤 3），具体分块由 P3-10 实现。
STEP_AFTER_PARSE = 3

# 旧格式 → 转换目标格式（8.1、K7）。
CONVERT_TARGETS = {
    "doc": "docx",
    "rtf": "docx",
    "odt": "docx",
    "ppt": "pptx",
    "odp": "pptx",
    "xls": "xlsx",
    "ods": "xlsx",
}
# 只有这几个引擎读不了旧格式（K7）。
CONVERTING_ENGINES = frozenset({"docling", "markitdown", "builtin"})
# Word、PPT 类文件另转一份预览 PDF（需求 4.1.8）。
PREVIEW_FILE_TYPES = frozenset({"word", "ppt"})

# 轮询间隔与单个作业超时（知识库技术方案 6.3）。
POLL_INTERVAL_SECONDS = 2.0
JOB_TIMEOUT_SECONDS = 1800.0
# 临时错误自动重试 3 次，间隔指数增长（8.3）。
MAX_PARSE_RETRIES = 3
RETRY_BASE_SECONDS = 1.0
# 解析服务作业失败的临时码（8.3；其余码按业务性错误立即失败）。
TEMPORARY_PARSE_CODES = frozenset({"TIMEOUT", "REMOTE_ERROR", "ENGINE_ERROR", "QUEUE_FULL"})


@dataclass
class _DocumentContext:
    """流水线开始处理前加载的文档上下文。"""

    doc_id: int
    kb_id: int
    file_type: str
    ext: str
    run_id: str | None
    run_by: int
    engine: str
    engine_params: dict[str, Any]
    preprocess: dict[str, Any]
    table: dict[str, Any]


class _PipelineFailure(Exception):
    """一步失败，携带步骤、错误码与面向用户的中文说明。"""

    def __init__(self, step: int, code: str, message: str, *, temporary: bool = False) -> None:
        self.step = step
        self.code = code
        self.message = message
        self.temporary = temporary
        super().__init__(message)


async def run_pipeline(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    doc_id: int,
    run_id: str | None,
    *,
    settings: Settings | None = None,
    storage: Storage | None = None,
    parser_client: ParserClient | None = None,
) -> None:
    """执行一个文档的入库流水线（步骤 1、2）。"""
    resolved = settings or get_settings()
    storage = storage or Storage(resolved)
    owns_client = parser_client is None
    client = parser_client or ParserClient(resolved)
    job_id: str | None = None

    async def _on_status(status: dict[str, Any]) -> None:
        nonlocal job_id
        job_id = str(status.get("id") or job_id or "")
        await _report_progress(sessionmaker, doc_id, run_id, status)

    try:
        context = await _load_context(sessionmaker, doc_id)
        if context is None:
            return
        if not context.engine:
            await _fail_document(
                sessionmaker,
                context,
                _PipelineFailure(
                    STEP_CONVERT,
                    "ENGINE_UNAVAILABLE",
                    "入库方案未配置可用的解析引擎",
                ),
            )
            return

        async with sessionmaker() as session:
            platform = await settings_service.get_settings(session)
            limit = platform.upload.parse_concurrency

        limiter = ParseTokenLimiter(redis)
        try:
            async with limiter.hold(limit):
                try:
                    source_key, source_ext = await _step_convert(
                        sessionmaker, storage, client, context
                    )
                    await _step_parse(
                        sessionmaker,
                        client,
                        context,
                        source_key=source_key,
                        source_ext=source_ext,
                        on_status=_on_status,
                    )
                except _PipelineFailure as failure:
                    await _fail_document(sessionmaker, context, failure)
                except StorageError:
                    await _fail_document(
                        sessionmaker,
                        context,
                        _PipelineFailure(STEP_CONVERT, "STORAGE_ERROR", "对象存储读写失败"),
                    )
        except ParseConcurrencyLimited as exc:
            # 拿不到全局解析并发令牌：稍后重试，不算失败、不计自动重试次数（8.2）。
            raise RetryLater() from exc
        except RunCancelled:
            await _cancel_job(client, job_id)
            raise
    finally:
        if owns_client:
            await client.aclose()


async def _load_context(
    sessionmaker: async_sessionmaker[AsyncSession], doc_id: int
) -> _DocumentContext | None:
    """加载文档与方案快照；没有可用方案时把文档标失败并返回 None。"""
    async with sessionmaker() as session:
        doc = await session.get(Document, doc_id)
        if doc is None:
            # 文档已被删除：等同于取消（8.3）。
            raise RunCancelled(f"文档 {doc_id} 不存在")
        snapshot = doc.parse_snapshot
        if not snapshot and doc.scheme_id is not None:
            scheme = await session.get(IngestScheme, doc.scheme_id)
            if scheme is not None:
                snapshot = scheme.config
                await session.execute(
                    sa.update(Document)
                    .where(Document.id == doc_id)
                    .values(parse_snapshot=snapshot, updated_at=sa.func.now())
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
        if not snapshot:
            await _fail_document(
                sessionmaker,
                _DocumentContext(
                    doc_id=doc_id,
                    kb_id=doc.kb_id,
                    file_type=doc.file_type,
                    ext=doc.ext,
                    run_id=doc.run_id,
                    run_by=doc.run_by,
                    engine="",
                    engine_params={},
                    preprocess={},
                    table={},
                ),
                _PipelineFailure(
                    STEP_CONVERT,
                    "SCHEME_UNAVAILABLE",
                    "入库方案不可用，请重新选择入库方案",
                ),
            )
            return None
        parse_config = snapshot.get("parse") or {}
        engine = str(parse_config.get("engine") or "")
        engine_params = {
            key: value
            for key, value in parse_config.items()
            if key not in ("engine", "preprocess") and value is not None
        }
        return _DocumentContext(
            doc_id=doc_id,
            kb_id=doc.kb_id,
            file_type=doc.file_type,
            ext=doc.ext,
            run_id=doc.run_id,
            run_by=doc.run_by,
            engine=engine,
            engine_params=engine_params,
            preprocess=parse_config.get("preprocess") or {},
            table=snapshot.get("table") or {},
        )


async def _step_convert(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: Storage,
    client: ParserClient,
    context: _DocumentContext,
) -> tuple[str, str]:
    """步骤 1：必要时转换文件并生成预览 PDF，返回解析用的源文件键与扩展名。"""
    await _set_step(sessionmaker, context, STEP_CONVERT, status="parsing", progress=0)
    original_key = kb_original_key(context.kb_id, context.doc_id, context.ext)
    if not await storage.exists(original_key):
        raise _PipelineFailure(STEP_CONVERT, "FILE_CORRUPTED", "原文件不存在或已被删除")
    source_key, source_ext = original_key, context.ext

    if context.engine in CONVERTING_ENGINES and context.ext in CONVERT_TARGETS:
        target = CONVERT_TARGETS[context.ext]
        output_key = doc_file_key(context.kb_id, context.doc_id, f"converted.{target}")
        try:
            await client.convert(
                {
                    "source_key": original_key,
                    "source_ext": context.ext,
                    "target": target,
                    "output_key": output_key,
                }
            )
        except ParserUnavailableError as exc:
            raise _PipelineFailure(
                STEP_CONVERT,
                "CONVERT_FAILED",
                "文件转换失败，无法继续解析",
            ) from exc
        source_key, source_ext = output_key, target

    if context.file_type in PREVIEW_FILE_TYPES:
        preview_key = doc_file_key(context.kb_id, context.doc_id, "preview.pdf")
        try:
            await client.convert(
                {
                    "source_key": source_key,
                    "source_ext": source_ext,
                    "target": "pdf",
                    "output_key": preview_key,
                }
            )
        except Exception:  # noqa: BLE001 - 预览失败只记录，不让文档失败（需求 4.1.8）
            logger.warning(
                "预览 PDF 转换失败，不影响入库: doc_id=%s", context.doc_id, exc_info=True
            )

    await _set_step(sessionmaker, context, STEP_CONVERT, status="parsing", progress=100)
    return source_key, source_ext


async def _step_parse(
    sessionmaker: async_sessionmaker[AsyncSession],
    client: ParserClient,
    context: _DocumentContext,
    *,
    source_key: str,
    source_ext: str,
    on_status: Any,
) -> None:
    """步骤 2：提交解析作业、轮询等待、失败重试，成功或失败都写回文档。"""
    await _set_step(sessionmaker, context, STEP_PARSE, status="parsing", progress=0)
    payload = {
        "source_key": source_key,
        "file_type": context.file_type,
        "ext": source_ext,
        "engine": context.engine,
        "engine_params": context.engine_params,
        "preprocess": context.preprocess,
        "table": context.table,
        "output_prefix": doc_file_key(context.kb_id, context.doc_id, "parsed/original"),
        "images_prefix": images_prefix(context.kb_id, context.doc_id),
        # MinerU 标准 / 高级档、PaddleOCR 的地址与密钥第六期从系统设置取。
        "remote": None,
    }

    failure: _PipelineFailure | None = None
    for attempt in range(MAX_PARSE_RETRIES + 1):
        await _check(sessionmaker, context)
        started = time.monotonic()
        try:
            status = await client.wait_job(
                payload,
                on_status=on_status,
                interval=POLL_INTERVAL_SECONDS,
                timeout=JOB_TIMEOUT_SECONDS,
            )
        except ParserJobTimeout as exc:
            await _cancel_job(client, exc.job_id)
            failure = _PipelineFailure(
                STEP_PARSE,
                "TIMEOUT",
                "解析超时，请稍后重试或换用更小的文档",
                temporary=True,
            )
        except ParserUnavailableError:
            failure = _PipelineFailure(
                STEP_PARSE,
                "PARSER_UNAVAILABLE",
                "解析服务暂时不可用，请稍后重试",
                temporary=True,
            )
        else:
            if status.get("status") == "succeeded":
                await _store_success(
                    sessionmaker,
                    context,
                    status,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                return
            error = status.get("error") or {}
            code = str(error.get("code") or "ENGINE_ERROR")
            failure = _PipelineFailure(
                STEP_PARSE,
                code,
                str(error.get("message") or "解析失败"),
                temporary=code in TEMPORARY_PARSE_CODES,
            )

        if not failure.temporary or attempt >= MAX_PARSE_RETRIES:
            break
        delay = RETRY_BASE_SECONDS * (2**attempt)
        logger.info(
            "解析失败，稍后重试: doc_id=%s attempt=%s code=%s",
            context.doc_id,
            attempt + 1,
            failure.code,
        )
        await asyncio.sleep(delay)

    if failure is None:  # pragma: no cover - 循环必然赋值，仅为类型收窄
        failure = _PipelineFailure(STEP_PARSE, "ENGINE_ERROR", "解析失败")
    await _fail_document(sessionmaker, context, failure)


async def _store_success(
    sessionmaker: async_sessionmaker[AsyncSession],
    context: _DocumentContext,
    status: dict[str, Any],
    *,
    duration_ms: int,
) -> None:
    """写解析结果：`document_parses`、`parse_records` 与文档页数 / 状态。"""
    result = status.get("result") or {}
    doc_key = result.get("doc_key") or doc_file_key(
        context.kb_id, context.doc_id, "parsed/original/doc.json"
    )
    md_key = result.get("md_key") or doc_file_key(
        context.kb_id, context.doc_id, "parsed/original/doc.md"
    )
    pages = result.get("pages")
    ocr_pages = result.get("ocr_pages")
    engine = str(result.get("engine") or context.engine)
    engine_version = result.get("engine_version")
    parser_duration = result.get("duration_ms")
    duration = int(parser_duration) if parser_duration is not None else duration_ms
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        await check_run_id(session, context.doc_id, context.run_id)
        values = {
            "doc_id": context.doc_id,
            "kind": "original",
            "doc_key": doc_key,
            "md_key": md_key,
            "engine": engine,
            "engine_version": engine_version,
            "pages": pages,
            "ocr_pages": ocr_pages,
            "created_by": context.run_by,
        }
        statement = pg_insert(DocumentParse).values(**values)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["doc_id", "kind"],
                set_={
                    "doc_key": doc_key,
                    "md_key": md_key,
                    "engine": engine,
                    "engine_version": engine_version,
                    "pages": pages,
                    "ocr_pages": ocr_pages,
                    "created_by": context.run_by,
                },
            )
        )
        session.add(
            ParseRecord(
                doc_id=context.doc_id,
                engine=engine,
                pages=pages,
                ocr_pages=ocr_pages,
                duration_ms=duration,
                ok=True,
                error_code=None,
            )
        )
        await session.execute(
            sa.update(Document)
            .where(Document.id == context.doc_id)
            .values(
                page_count=pages,
                status="chunking",
                step=STEP_AFTER_PARSE,
                progress=0,
                failed_step=None,
                error_code=None,
                error_message=None,
                heartbeat_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def _fail_document(
    sessionmaker: async_sessionmaker[AsyncSession],
    context: _DocumentContext,
    failure: _PipelineFailure,
) -> None:
    """写失败状态；解析步骤失败时补一条 `parse_records`。"""
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        if failure.step == STEP_PARSE:
            session.add(
                ParseRecord(
                    doc_id=context.doc_id,
                    engine=context.engine or "unknown",
                    pages=None,
                    ocr_pages=None,
                    duration_ms=None,
                    ok=False,
                    error_code=failure.code,
                )
            )
        await session.execute(
            sa.update(Document)
            .where(Document.id == context.doc_id)
            .values(
                status="failed",
                step=failure.step,
                failed_step=failure.step,
                error_code=failure.code,
                error_message=failure.message,
                heartbeat_at=now,
                finished_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    logger.info(
        "入库失败: doc_id=%s step=%s code=%s message=%s",
        context.doc_id,
        failure.step,
        failure.code,
        failure.message,
    )


async def _set_step(
    sessionmaker: async_sessionmaker[AsyncSession],
    context: _DocumentContext,
    step: int,
    *,
    status: str,
    progress: int,
) -> None:
    """写回当前步骤、状态与心跳（8.1：每一步都写回）。"""
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        await check_run_id(session, context.doc_id, context.run_id)
        await session.execute(
            sa.update(Document)
            .where(Document.id == context.doc_id)
            .values(
                step=step,
                status=status,
                progress=progress,
                heartbeat_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def _report_progress(
    sessionmaker: async_sessionmaker[AsyncSession],
    doc_id: int,
    run_id: str | None,
    status: dict[str, Any],
) -> None:
    """把解析作业的进度写回 `documents.progress` 并更新心跳；顺带检查取消。"""
    progress = status.get("progress") or {}
    done = int(progress.get("done_pages") or 0)
    total = int(progress.get("total_pages") or 0)
    percent = int(done * 100 / total) if total else 0
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        await check_run_id(session, doc_id, run_id)
        await session.execute(
            sa.update(Document)
            .where(Document.id == doc_id)
            .values(progress=percent, heartbeat_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def _check(sessionmaker: async_sessionmaker[AsyncSession], context: _DocumentContext) -> None:
    """每步开始前检查 `run_id`（8.3）。"""
    async with sessionmaker() as session:
        await check_run_id(session, context.doc_id, context.run_id)


async def _cancel_job(client: ParserClient, job_id: str | None) -> None:
    """取消解析服务上的作业（8.3）；取消失败只记日志。"""
    if not job_id:
        return
    try:
        await client.cancel_job(job_id)
    except ParserUnavailableError:
        logger.warning("取消解析作业失败: job_id=%s", job_id)
