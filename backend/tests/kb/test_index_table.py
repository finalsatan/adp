"""每库索引表：维度、索引、中文全文召回、向量召回、删表（P3-03）。"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.kb.index_table import (
    INDEX_SCHEMA_VERSION,
    create_index_table,
    drop_index_table,
    index_table_exists,
    index_table_name,
)


def _vector_literal(dim: int, value: float = 0.0) -> str:
    return "[" + ",".join([str(value)] * dim) + "]"


async def _embedding_type(session: AsyncSession, kb_id: int) -> str | None:
    return await session.scalar(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = to_regclass(:t) AND attname = 'embedding'"
        ),
        {"t": index_table_name(kb_id)},
    )


async def _insert_entry(
    session: AsyncSession,
    kb_id: int,
    *,
    chunk_id: int,
    body: str,
    title: str | None = None,
    keywords: str | None = None,
    active: bool = True,
    dim: int = 1024,
    embedding_value: float = 0.1,
) -> None:
    await session.execute(
        text(
            f"INSERT INTO {index_table_name(kb_id)} "
            "(entry_type, chunk_id, doc_id, generation, active, embedding, title, body, keywords) "
            "VALUES ('chunk', :chunk_id, 1, 1, :active, :embedding, :title, :body, :keywords)"
        ),
        {
            "chunk_id": chunk_id,
            "active": active,
            "embedding": _vector_literal(dim, embedding_value),
            "title": title,
            "body": body,
            "keywords": keywords,
        },
    )


def test_index_schema_version() -> None:
    assert INDEX_SCHEMA_VERSION == 1
    assert index_table_name(5) == "kb_index.kb_5"


async def test_dimension_selects_vector_or_halfvec(session: AsyncSession) -> None:
    await create_index_table(session, 1, 1024)
    await session.commit()
    assert await _embedding_type(session, 1) == "vector(1024)"

    await create_index_table(session, 2, 2560)
    await session.commit()
    assert await _embedding_type(session, 2) == "halfvec(2560)"

    with pytest.raises(ValueError):
        await create_index_table(session, 3, 4001)


async def test_indexes_created(session: AsyncSession) -> None:
    await create_index_table(session, 5, 1024)
    await session.commit()

    rows = await session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'kb_index' AND tablename = 'kb_5'")
    )
    defs = [row[0] for row in rows]
    hnsw = [d for d in defs if "USING hnsw" in d]
    bm25 = [d for d in defs if "USING paradedb" in d]
    assert len(hnsw) == 1, defs
    assert "vector_cosine_ops" in hnsw[0]

    assert len(bm25) == 1, defs
    for field in ("title", "body", "keywords", "entry_type", "doc_id", "active"):
        assert field in bm25[0], bm25[0]


async def test_fulltext_recall_chinese(session: AsyncSession) -> None:
    await create_index_table(session, 7, 1024)
    await session.commit()
    await _insert_entry(
        session,
        7,
        chunk_id=1,
        title="差旅费报销制度",
        body="南京市长江大桥附近的酒店住宿上限为每晚五百元。",
        keywords="差旅,报销",
    )
    await _insert_entry(
        session,
        7,
        chunk_id=2,
        title="停用条目",
        body="长江大桥封闭施工。",
        active=False,
    )
    await session.commit()

    # 全文召回参数显式转 text（X09 报告 6a、完成记录第 1 条）：同一参数同时出现在带 boost
    # 和不带 boost 的位置时，不显式转 text 会报 invalid typemod -1。
    # 注意 `:q ::text` 里的空格：SQLAlchemy 的 text() 在 `:q` 紧跟 `::` 时不会把 `:q` 当绑定
    # 参数（会原样发给服务端报语法错），加空格等价于 `:q::text`。P3-13 写检索时照此处理。
    result = await session.execute(
        text(
            "SELECT id, chunk_id, body, pdb.score(id) AS score FROM kb_index.kb_7 "
            "WHERE (title ||| :q ::text::pdb.boost(2) OR body ||| :q ::text "
            "       OR keywords ||| :q ::text::pdb.boost(2)) "
            "AND active ORDER BY pdb.score(id) DESC LIMIT 5"
        ),
        {"q": "大桥"},
    )
    rows = result.all()
    assert rows, "全文召回没有结果"
    # 搜“大桥”能召回含“长江大桥”的条目（jieba 搜索模式拆词）。
    assert any("长江大桥" in row.body for row in rows)
    # active=false 的条目不参与召回。
    assert all(row.chunk_id != 2 for row in rows)


async def test_vector_recall(session: AsyncSession) -> None:
    await create_index_table(session, 9, 1024)
    await session.commit()
    await _insert_entry(session, 9, chunk_id=1, body="长江大桥", dim=1024)
    await session.commit()

    await session.execute(text("SET LOCAL hnsw.ef_search = 20"))
    result = await session.execute(
        text(
            "SELECT id FROM kb_index.kb_9 WHERE active ORDER BY embedding <=> :v ::vector LIMIT 5"
        ),
        {"v": _vector_literal(1024, 0.2)},
    )
    assert result.all()


async def test_drop_index_table(session: AsyncSession) -> None:
    await create_index_table(session, 11, 1024)
    await session.commit()
    assert await index_table_exists(session, 11)

    await drop_index_table(session, 11)
    await session.commit()
    assert not await index_table_exists(session, 11)

    # 表不存在时再删也不报错。
    await drop_index_table(session, 11)
    await session.commit()
