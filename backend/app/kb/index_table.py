"""每个知识库一张索引表（知识库技术方案 11.3）。

DDL 照 `scripts/paradedb_check/REPORT.md` 在真实 ParadeDB 镜像（0.26.2-pg16）上实测通过的写法。
建表 / 删表由应用账号执行：初始化脚本已给 `adp_app` 对 `kb_index` schema 的 `CREATE` 权限（P1-01）。

注意：DDL 不能参数化，表名直接拼进 SQL；`kb_id`、`dim` 都是整数，拼之前用 `int()` 校验。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

# knowledge_bases.index_schema_version 记录的版本号。
INDEX_SCHEMA_VERSION = 1
INDEX_SCHEMA = "kb_index"

# pgvector HNSW 的上限：2000 维以内用 vector，2000–4000 维用 halfvec，超过 4000 直接报错
# （技术方案 9.1 在模型登记时就拒绝，这里再兜底）。
_VECTOR_MAX_DIM = 2000
_MAX_DIM = 4000

Connection = AsyncConnection | AsyncSession


def index_table_name(kb_id: int) -> str:
    """索引表的全限定名，形如 `kb_index.kb_5`。"""
    return f"{INDEX_SCHEMA}.kb_{int(kb_id)}"


def _embedding_column(dim: int) -> tuple[str, str]:
    """返回 (列类型, HNSW 操作符类)。"""
    if dim <= _VECTOR_MAX_DIM:
        return f"vector({dim})", "vector_cosine_ops"
    return f"halfvec({dim})", "halfvec_cosine_ops"


async def create_index_table(conn: Connection, kb_id: int, dim: int) -> None:
    """建索引表、HNSW 索引、BM25 索引与 doc_id / chunk_id 的 B-tree。"""
    kb_id = int(kb_id)
    dim = int(dim)
    if dim < 1 or dim > _MAX_DIM:
        raise ValueError(f"向量维度 {dim} 超出范围（1–{_MAX_DIM}）")

    table = index_table_name(kb_id)
    embedding_type, opclass = _embedding_column(dim)

    await conn.execute(
        text(
            f"""
            CREATE TABLE {table} (
                id bigserial PRIMARY KEY,
                entry_type text NOT NULL,
                chunk_id bigint,
                qa_id bigint,
                doc_id bigint,
                generation int NOT NULL,
                active boolean NOT NULL,
                embedding {embedding_type},
                title text,
                body text NOT NULL,
                keywords text
            )
            """
        )
    )
    await conn.execute(
        text(f"CREATE INDEX kb_{kb_id}_hnsw ON {table} USING hnsw (embedding {opclass})")
    )
    # ParadeDB 索引是覆盖索引，字段一次定全，增删字段要 REINDEX（知识库技术方案 11.3）。
    await conn.execute(
        text(
            f"CREATE INDEX kb_{kb_id}_bm25 ON {table} USING paradedb ("
            "id, (title::pdb.jieba), (body::pdb.jieba), (keywords::pdb.jieba), "
            "entry_type, doc_id, active)"
        )
    )
    await conn.execute(text(f"CREATE INDEX kb_{kb_id}_doc_id ON {table} (doc_id)"))
    await conn.execute(text(f"CREATE INDEX kb_{kb_id}_chunk_id ON {table} (chunk_id)"))


async def drop_index_table(conn: Connection, kb_id: int) -> None:
    """删知识库时调用；表不存在也不报错。"""
    await conn.execute(text(f"DROP TABLE IF EXISTS {index_table_name(kb_id)}"))


async def index_table_exists(conn: Connection, kb_id: int) -> bool:
    result = await conn.execute(
        text("SELECT to_regclass(:name)"), {"name": index_table_name(kb_id)}
    )
    return result.scalar() is not None
