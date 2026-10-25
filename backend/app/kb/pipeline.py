"""入库流水线：步骤 1 转换、步骤 2 解析（P3-09，知识库技术方案 8.1、8.3）。

- 步骤 1 转换：选 Docling、MarkItDown、内置表格解析时，旧格式（doc、rtf、odt、
  ppt、odp、xls、ods）先调解析服务 `POST /v1/convert` 转成 docx / pptx / xlsx；
  Word、PPT 类另转一份预览 PDF，**预览失败只记录日志**（需求 4.1.8）。
- 步骤 2 解析：提交解析服务作业、轮询等待、写回进度与心跳；临时错误自动重试
  3 次，业务性错误立即失败；每步开始前检查 `run_id`，变化时取消解析作业并退出。
- 成功写 `document_parses`、`parse_records` 和 `documents` 的页数 / 状态 / 步骤。

分块（步骤 3）、向量化（步骤 5）与两代切换（步骤 6）见 P3-10 / P3-11。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from docling_core.types.doc.document import DoclingDocument
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import (
    ChunkTooLongError,
    ModelTypeMismatchError,
    ModelUnavailableError,
    ParserUnavailableError,
    StorageError,
)
from app.core.storage import (
    Storage,
    doc_file_key,
    images_prefix,
    kb_original_key,
)
from app.gateway.errors import ModelCallError
from app.iam.tables import User
from app.kb import indexing
from app.kb.chunking import chunk_document, write_chunks
from app.kb.parser_client import ParserClient, ParserJobTimeout
from app.kb.schemas import SchemeConfig
from app.kb.tables import (
    Chunk,
    Document,
    DocumentParse,
    IngestScheme,
    KnowledgeBase,
    ParseRecord,
)
from app.models.tables import Model
from app.settings import service as settings_service
from app.worker.dispatch import RunCancelled, check_run_id
from app.worker.tokens import ParseConcurrencyLimited, ParseTokenLimiter, RetryLater

logger = logging.getLogger(__name__)

# 入库步骤序号（知识库技术方案 8.1）。
STEP_CONVERT = 1
STEP_PARSE = 2
STEP_CHUNK = 3
STEP_ENHANCE = 4
STEP_EMBED = 5
STEP_ACTIVATE = 6
# 解析完成后进入分块（步骤 3）。
STEP_AFTER_PARSE = STEP_CHUNK

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
    # 完整方案快照，分块（步骤 3）用。
    snapshot: dict[str, Any]
    # 本次入库写的新一代号与是否附带标题路径，由步骤 3 填。
    generation: int = 0
    title_path_enabled: bool = True


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
    """执行一个文档的入库流水线（步骤 1、2、3、5、6）。"""
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
                    await _step_chunk(sessionmaker, storage, context)
                    await _step_embed(sessionmaker, redis, context)
                    await _step_activate(sessionmaker, context)
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
                    snapshot={},
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
            snapshot=snapshot,
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
    # 交给 run_pipeline 统一调 _fail_document（失败步骤、解析记录都在那里写）。
    raise failure


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


# ---- 步骤 3：分块 ----


def _scheme_config(context: _DocumentContext) -> SchemeConfig:
    try:
        return SchemeConfig.model_validate(context.snapshot)
    except ValidationError as exc:
        raise _PipelineFailure(STEP_CHUNK, "SCHEME_UNAVAILABLE", "入库方案配置不正确") from exc


async def _load_embedding_model(
    sessionmaker: async_sessionmaker[AsyncSession], kb_id: int, step: int
) -> Model:
    async with sessionmaker() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        if kb is None:
            raise _PipelineFailure(step, "KB_NOT_FOUND", "知识库不存在")
        model = await session.get(Model, kb.embedding_model_id)
        if model is None or model.deleted_at is not None or model.status != "enabled":
            raise _PipelineFailure(step, "MODEL_UNAVAILABLE", "知识库的向量化模型不可用")
        return model


async def _step_chunk(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: Storage,
    context: _DocumentContext,
) -> None:
    """步骤 3：解析结果 → 新一代分块（未生效），做超长检查（8.1、9.4）。"""
    await _set_step(sessionmaker, context, STEP_CHUNK, status="chunking", progress=0)
    config = _scheme_config(context)
    model = await _load_embedding_model(sessionmaker, context.kb_id, STEP_CHUNK)
    async with sessionmaker() as session:
        await check_run_id(session, context.doc_id, context.run_id)
        doc = await session.get(Document, context.doc_id)
        parse = await session.get(DocumentParse, (context.doc_id, "original"))
        doc_key = parse.doc_key if parse is not None else None
        generation = (doc.current_generation if doc is not None else 0) + 1
    if not doc_key:
        raise _PipelineFailure(STEP_CHUNK, "FILE_CORRUPTED", "找不到解析结果，无法分块")
    try:
        raw = await storage.get_bytes(doc_key)
    except StorageError as exc:
        raise _PipelineFailure(STEP_CHUNK, "STORAGE_ERROR", "对象存储读写失败") from exc
    try:
        docling = DoclingDocument.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 - 解析产物损坏
        raise _PipelineFailure(STEP_CHUNK, "FILE_CORRUPTED", "解析结果损坏，无法分块") from exc
    chunks = chunk_document(docling, config)
    try:
        indexing.check_within_limit(chunks, model, title_path_enabled=config.chunk.title_path)
    except ChunkTooLongError as exc:
        raise _PipelineFailure(STEP_CHUNK, exc.code, str(exc)) from exc

    context.generation = generation
    context.title_path_enabled = config.chunk.title_path
    async with sessionmaker() as session:
        await check_run_id(session, context.doc_id, context.run_id)
        await write_chunks(
            session,
            kb_id=context.kb_id,
            doc_id=context.doc_id,
            generation=generation,
            chunks=chunks,
            run_by=context.run_by,
        )
        await session.commit()


# ---- 步骤 5：向量化并写索引 ----


async def _step_embed(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    context: _DocumentContext,
) -> None:
    """步骤 5：分批向量化，写入新一代索引条目（`active=false`），进度写回文档。"""
    await _set_step(sessionmaker, context, STEP_EMBED, status="embedding", progress=0)
    async with sessionmaker() as session:
        await check_run_id(session, context.doc_id, context.run_id)
        kb = await session.get(KnowledgeBase, context.kb_id)
        model = await session.get(Model, kb.embedding_model_id) if kb is not None else None
        user = await session.get(User, context.run_by)
        org_id = user.org_id if user is not None else None
        rows = list(
            (
                await session.execute(
                    sa.select(Chunk)
                    .where(
                        Chunk.doc_id == context.doc_id,
                        Chunk.generation == context.generation,
                    )
                    .order_by(Chunk.seq)
                )
            )
            .scalars()
            .all()
        )
    if kb is None:
        raise _PipelineFailure(STEP_EMBED, "KB_NOT_FOUND", "知识库不存在")
    if model is None or model.deleted_at is not None:
        raise _PipelineFailure(STEP_EMBED, "MODEL_UNAVAILABLE", "知识库的向量化模型不可用")
    if not rows:
        raise _PipelineFailure(STEP_EMBED, "CHUNK_EMPTY", "没有可向量化的分块")

    async def on_progress(done: int, total: int) -> None:
        percent = int(done * 100 / total) if total else 0
        await _set_step(sessionmaker, context, STEP_EMBED, status="embedding", progress=percent)

    try:
        await indexing.index_chunks(
            sessionmaker,
            redis,
            kb_id=context.kb_id,
            doc_id=context.doc_id,
            generation=context.generation,
            run_by=context.run_by,
            org_id=org_id,
            model=model,
            chunks=rows,
            title_path_enabled=context.title_path_enabled,
            on_progress=on_progress,
        )
    except ChunkTooLongError as exc:
        raise _PipelineFailure(STEP_EMBED, exc.code, str(exc)) from exc
    except ModelUnavailableError as exc:
        raise _PipelineFailure(
            STEP_EMBED, "MODEL_UNAVAILABLE", "向量化模型不可用（已停用或已删除）"
        ) from exc
    except ModelTypeMismatchError as exc:
        raise _PipelineFailure(
            STEP_EMBED, "MODEL_TYPE_MISMATCH", "知识库的向量化模型类型不正确"
        ) from exc
    except ModelCallError as exc:
        raise _PipelineFailure(
            STEP_EMBED, exc.error_code, "向量化失败，请稍后重试", temporary=exc.retryable
        ) from exc


# ---- 步骤 6：生效与两代切换 ----


async def _step_activate(
    sessionmaker: async_sessionmaker[AsyncSession], context: _DocumentContext
) -> None:
    """步骤 6：一个事务里切换新旧两代、更新计数与文档状态（8.4）。"""
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        await check_run_id(session, context.doc_id, context.run_id)
        doc = await session.get(Document, context.doc_id)
        if doc is None:
            raise RunCancelled(f"文档 {context.doc_id} 不存在")
        chunk_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Chunk)
            .where(
                Chunk.doc_id == context.doc_id,
                Chunk.generation == context.generation,
            )
        )
        await indexing.activate_generation(
            session,
            kb_id=context.kb_id,
            doc_id=context.doc_id,
            generation=context.generation,
            chunk_count=int(chunk_count or 0),
            enabled=doc.enabled,
        )
        await session.execute(
            sa.update(Document)
            .where(Document.id == context.doc_id)
            .values(
                status="done",
                step=STEP_ACTIVATE,
                progress=100,
                finished_at=now,
                heartbeat_at=now,
                failed_step=None,
                error_code=None,
                error_message=None,
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
