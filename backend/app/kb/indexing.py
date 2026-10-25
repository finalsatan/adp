"""向量化、索引写入与新旧两代切换（P3-11，知识库技术方案 8.1、8.4、9.4、11.3、11.4）。

- `embed_chunks`：向量化文本 = 标题路径（方案开启“附带标题路径”时）+ 上下文说明 +
  分块内容（9.4）。按向量化模型的 `embedding_batch_size` 分批，走
  `app.gateway.service.embed`（限流、入库额度、用量都记在 `source='ingest'`）；
  用 `asyncio.gather` 控制并发。入库额度限流（`RateLimited`）时抛 `RetryLater`，
  由任务稍后重试，不算失败、不计自动重试次数（8.3）。
- `write_index_entries`：把新一代分块写入每库索引表，`entry_type='chunk'`、
  `active=false`（8.1 步骤 5、8.4）。BM25 索引随写入自动更新。
- `activate_generation`：在**一个事务**里把旧一代索引条目 `active=false`、新一代
  `active=true`、`documents.current_generation` 改为新值、更新文档和知识库计数（8.4）。
- `set_doc_active` / `set_chunk_active`：启停文档或分块时更新索引条目的 `active`
  （8.4；P3-12 调用）。
- `delete_old_generations`：maintenance 任务删除旧一代的分块与索引条目（P3-12 调用）。
- `cleanup_old_generations`：maintenance 任务入口，找出已生效文档并分批调用上面的删除。
- `FULLTEXT_CONDITION`：P3-13 检索用的全文召回条件（X09 实测写法）。

`body` = 上下文说明 + 分块内容；`title` 在方案开启“附带标题路径”时存标题路径，
否则为空；`keywords` 本阶段为空（分块增强第六期）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.core.errors import ChunkTooLongError
from app.gateway import service as gateway
from app.gateway.ratelimit import RateLimited
from app.gateway.service import CallMeta
from app.kb.chunking import ChunkData, ensure_within_limit
from app.kb.index_table import index_table_name
from app.kb.tables import Chunk, Document, KnowledgeBase
from app.models.tables import Model
from app.worker.dispatch import PROCESSING_STATUSES
from app.worker.tokens import RetryLater

logger = logging.getLogger(__name__)

Connection = AsyncConnection | AsyncSession
SessionMaker = async_sessionmaker[AsyncSession]
ProgressCallback = Callable[[int, int], Awaitable[None]]

# 分批向量化的并发（知识库技术方案 8.2：同一任务内的并发用 asyncio.gather）。
EMBED_CONCURRENCY = 4

# 检索用的全文召回条件（X09 报告 6a 实测写法）。知识库技术方案 11.4 原文漏了
# `::text`，直接照原文会在 psycopg3 下报 `invalid typemod -1`；另外 `:q` 与 `::`
# 之间要留空格，否则 SQLAlchemy 的 text() 不把 `:q` 当绑定参数。
FULLTEXT_CONDITION = (
    "title ||| :q ::text::pdb.boost(2) OR body ||| :q ::text "
    "OR keywords ||| :q ::text::pdb.boost(2)"
)


def embedding_text(chunk: ChunkData, *, title_path_enabled: bool, context: str = "") -> str:
    """向量化文本（9.4）：标题路径（开启时）+ 上下文说明 + 分块内容。"""
    parts: list[str] = []
    if title_path_enabled and chunk.title_path:
        parts.append(chunk.title_path)
    if context:
        parts.append(context)
    parts.append(chunk.content)
    return "\n".join(parts)


def body_text(chunk: ChunkData, *, context: str = "") -> str:
    """索引条目的 `body`（11.3）：上下文说明 + 分块内容；本阶段没有增强，上下文为空。"""
    return f"{context}\n{chunk.content}" if context else chunk.content


def _vector_literal(vector: Sequence[float]) -> str:
    """pgvector 参数用字符串字面量传（与 P3-03 测试一致，列类型决定 vector / halfvec）。"""
    return "[" + ",".join(str(float(value)) for value in vector) + "]"


def _to_chunk_data(chunk: Chunk) -> ChunkData:
    return ChunkData(
        seq=chunk.seq,
        kind=chunk.kind,
        title_path=chunk.title_path,
        content=chunk.content,
        token_count=chunk.token_count or 0,
        positions=list(chunk.positions or []),
    )


def check_within_limit(
    chunks: Sequence[ChunkData], model: Model, *, title_path_enabled: bool
) -> None:
    """向量化前按实际内容再检查一次 90% 上限（9.4）。模型未设最大输入时跳过。"""
    if not model.embedding_max_input:
        return
    ensure_within_limit(
        chunks,
        embedding_max_input=model.embedding_max_input,
        title_path_enabled=title_path_enabled,
    )


async def embed_chunks(
    sessionmaker: SessionMaker,
    redis: Redis,
    *,
    model: Model,
    meta: CallMeta,
    chunks: Sequence[ChunkData],
    title_path_enabled: bool,
    context: str = "",
    on_progress: ProgressCallback | None = None,
) -> list[list[float]]:
    """按 `embedding_batch_size` 分批向量化，返回与 `chunks` 一一对应的向量。"""
    if not chunks:
        return []
    texts = [
        embedding_text(chunk, title_path_enabled=title_path_enabled, context=context)
        for chunk in chunks
    ]
    batch_size = max(1, model.embedding_batch_size or len(texts))
    batches = [texts[start : start + batch_size] for start in range(0, len(texts), batch_size)]
    vectors: list[list[float] | None] = [None] * len(texts)
    semaphore = asyncio.Semaphore(EMBED_CONCURRENCY)
    progress_lock = asyncio.Lock()
    done = 0

    async def run(start: int, batch: list[str]) -> None:
        nonlocal done
        async with semaphore:
            try:
                result = await gateway.embed(sessionmaker, redis, meta, model.id, batch)
            except RateLimited as exc:
                raise RetryLater() from exc
        for offset, vector in enumerate(result.vectors):
            vectors[start + offset] = vector
        async with progress_lock:
            done += len(batch)
            if on_progress is not None:
                await on_progress(done, len(texts))

    results = await asyncio.gather(
        *(run(index * batch_size, batch) for index, batch in enumerate(batches)),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result

    missing = [index for index, vector in enumerate(vectors) if vector is None]
    if missing:
        # 上游返回的向量条数不足；这不是业务错误，直接暴露出来。
        raise ChunkTooLongError(f"向量化返回的向量条数不足（缺少 {len(missing)} 条）")
    return [vector for vector in vectors if vector is not None]


async def write_index_entries(
    session: AsyncSession,
    *,
    kb_id: int,
    doc_id: int,
    generation: int,
    chunks: Sequence[ChunkData],
    vectors: Sequence[Sequence[float]],
    chunk_ids: Sequence[int],
    title_path_enabled: bool,
    context: str = "",
) -> None:
    """把新一代索引条目写入每库索引表，`active=false`（11.3、8.4）。调用方负责 commit。"""
    if not chunks:
        return
    table = index_table_name(kb_id)
    params = [
        {
            "chunk_id": chunk_id,
            "doc_id": doc_id,
            "generation": generation,
            "embedding": _vector_literal(vector),
            "title": chunk.title_path if title_path_enabled else None,
            "body": body_text(chunk, context=context),
        }
        for chunk, vector, chunk_id in zip(chunks, vectors, chunk_ids, strict=True)
    ]
    await session.execute(
        text(
            f"INSERT INTO {table} "
            "(entry_type, chunk_id, doc_id, generation, active, embedding, title, body, keywords) "
            "VALUES ('chunk', :chunk_id, :doc_id, :generation, false, "
            ":embedding, :title, :body, NULL)"
        ),
        params,
    )


async def index_chunks(
    sessionmaker: SessionMaker,
    redis: Redis,
    *,
    kb_id: int,
    doc_id: int,
    generation: int,
    run_by: int,
    org_id: int | None,
    model: Model,
    chunks: Sequence[Chunk],
    title_path_enabled: bool,
    context: str = "",
    on_progress: ProgressCallback | None = None,
) -> int:
    """向量化新一代分块并写入索引表（`active=false`）；返回写入条数。"""
    chunk_data = [_to_chunk_data(chunk) for chunk in chunks]
    check_within_limit(chunk_data, model, title_path_enabled=title_path_enabled)
    meta = CallMeta(source="ingest", source_id=doc_id, user_id=run_by, org_id=org_id)
    vectors = await embed_chunks(
        sessionmaker,
        redis,
        model=model,
        meta=meta,
        chunks=chunk_data,
        title_path_enabled=title_path_enabled,
        context=context,
        on_progress=on_progress,
    )
    async with sessionmaker() as session:
        await write_index_entries(
            session,
            kb_id=kb_id,
            doc_id=doc_id,
            generation=generation,
            chunks=chunk_data,
            vectors=vectors,
            chunk_ids=[chunk.id for chunk in chunks],
            title_path_enabled=title_path_enabled,
            context=context,
        )
        await session.commit()
    return len(chunk_data)


async def activate_generation(
    session: AsyncSession,
    *,
    kb_id: int,
    doc_id: int,
    generation: int,
    chunk_count: int,
    enabled: bool,
) -> None:
    """切换新旧两代并更新计数（8.4）；调用方负责 commit，保证与状态更新同一事务。

    - 本文档旧一代的索引条目 `active=false`，新一代 `active = documents.enabled`；
    - `documents.current_generation` 改为新值、`chunk_count` 更新；
    - 知识库的 `doc_count` / `chunk_count` 重算。
    """
    table = index_table_name(kb_id)
    await session.execute(
        text(f"UPDATE {table} SET active = false WHERE doc_id = :doc_id AND generation <> :gen"),
        {"doc_id": doc_id, "gen": generation},
    )
    await session.execute(
        text(f"UPDATE {table} SET active = :enabled WHERE doc_id = :doc_id AND generation = :gen"),
        {"doc_id": doc_id, "gen": generation, "enabled": enabled},
    )
    await session.execute(
        sa.update(Document)
        .where(Document.id == doc_id)
        .values(
            current_generation=generation,
            chunk_count=chunk_count,
            updated_at=sa.func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    await refresh_kb_counts(session, kb_id)


async def refresh_kb_counts(session: AsyncSession, kb_id: int) -> None:
    """重算知识库的文档数与分块数（文档数算本库全部文档，分块数算各文档的 `chunk_count`）。"""
    doc_count = await session.scalar(
        sa.select(sa.func.count()).select_from(Document).where(Document.kb_id == kb_id)
    )
    chunk_count = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(Document.chunk_count), 0)).where(
            Document.kb_id == kb_id
        )
    )
    await session.execute(
        sa.update(KnowledgeBase)
        .where(KnowledgeBase.id == kb_id)
        .values(
            doc_count=int(doc_count or 0),
            chunk_count=int(chunk_count or 0),
            updated_at=sa.func.now(),
        )
        .execution_options(synchronize_session=False)
    )


async def set_doc_active(conn: Connection, kb_id: int, doc_id: int, enabled: bool) -> None:
    """启停文档时更新索引条目的 `active`（8.4；P3-12 调用）。

    停用：该文档所有代的条目都置 false；启用：只把当前代的条目置 true。
    """
    table = index_table_name(kb_id)
    if enabled:
        await conn.execute(
            text(
                f"UPDATE {table} i SET active = true FROM public.documents d "
                "WHERE i.doc_id = d.id AND i.doc_id = :doc_id "
                "AND i.generation = d.current_generation"
            ),
            {"doc_id": doc_id},
        )
    else:
        await conn.execute(
            text(f"UPDATE {table} SET active = false WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )


async def set_chunk_active(conn: Connection, kb_id: int, chunk_id: int, enabled: bool) -> None:
    """启停分块时更新索引条目的 `active`（8.4；P3-12 调用）。

    启用时要求所属文档启用且是当前代，避免把停用文档或旧代的分块放回检索。
    """
    table = index_table_name(kb_id)
    if enabled:
        await conn.execute(
            text(
                f"UPDATE {table} i SET active = true "
                "FROM public.chunks c, public.documents d "
                "WHERE i.chunk_id = c.id AND i.chunk_id = :chunk_id "
                "AND d.id = c.doc_id AND i.generation = d.current_generation AND d.enabled"
            ),
            {"chunk_id": chunk_id},
        )
    else:
        await conn.execute(
            text(f"UPDATE {table} SET active = false WHERE chunk_id = :chunk_id"),
            {"chunk_id": chunk_id},
        )


async def delete_old_generations(
    session: AsyncSession, kb_id: int, doc_id: int, current_generation: int
) -> None:
    """删除旧一代的分块与索引条目（8.4；maintenance 任务调用，调用方负责 commit）。"""
    await session.execute(
        text(
            f"DELETE FROM {index_table_name(kb_id)} WHERE doc_id = :doc_id AND generation <> :gen"
        ),
        {"doc_id": doc_id, "gen": current_generation},
    )
    await session.execute(
        sa.delete(Chunk).where(Chunk.doc_id == doc_id, Chunk.generation != current_generation)
    )


# 旧一代清理（8.4）：单次任务最多处理的文档数、每次查询取的文档数（补充）。
CLEANUP_DOC_LIMIT = 500
CLEANUP_BATCH_SIZE = 100


async def cleanup_old_generations(
    sessionmaker: SessionMaker,
    *,
    limit: int = CLEANUP_DOC_LIMIT,
    batch_size: int = CLEANUP_BATCH_SIZE,
) -> int:
    """删除旧一代分块与索引条目（8.4；maintenance 任务调用）。

    - 只处理没有在跑入库、也没有待生效新一代的文档：处理中 / 失败的文档，以及还有
      比当前代更新的分块（重新解析断点续跑要用的新一代）都跳过，避免删到正在生成
      或待生效的条目（8.4）；
    - 分批查询、单次最多处理 `limit` 个文档，避免一次事务过大、长时间占用数据库；
    - 幂等：旧一代删掉后文档不再被选中，重复执行不会重复删除。
    """
    cleaned = 0
    while cleaned < limit:
        size = min(batch_size, limit - cleaned)
        async with sessionmaker() as session:
            old_chunk = (
                sa.select(Chunk.id)
                .where(
                    Chunk.doc_id == Document.id,
                    Chunk.generation < Document.current_generation,
                )
                .exists()
            )
            newer_chunk = (
                sa.select(Chunk.id)
                .where(
                    Chunk.doc_id == Document.id,
                    Chunk.generation > Document.current_generation,
                )
                .exists()
            )
            rows = (
                await session.execute(
                    sa.select(Document.id, Document.kb_id, Document.current_generation)
                    .where(
                        Document.status.notin_((*PROCESSING_STATUSES, "failed")),
                        old_chunk,
                        ~newer_chunk,
                    )
                    .order_by(Document.id)
                    .limit(size)
                )
            ).all()
        if not rows:
            break
        for doc_id, kb_id, generation in rows:
            async with sessionmaker() as session:
                await delete_old_generations(session, kb_id, doc_id, generation)
                await session.commit()
            cleaned += 1
    if cleaned:
        logger.info("清理旧一代分块与索引条目: %s 个文档", cleaned)
    return cleaned
