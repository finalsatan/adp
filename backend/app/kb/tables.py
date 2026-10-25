"""知识库（M2）业务表（知识库技术方案 11.2）。

字段照 11.2 与 P3-03 卡片；问答库两张表（qa_categories、qa_pairs）第六期再建。
每库一张索引表不在 ORM 里，见 app/kb/index_table.py。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

FILE_TYPES = (
    "pdf",
    "word",
    "ppt",
    "excel",
    "csv",
    "markdown",
    "txt",
    "html",
    "epub",
    "ofd",
    "image",
)
DOCUMENT_STATUSES = (
    "queued",
    "parsing",
    "chunking",
    "enhancing",
    "embedding",
    "done",
    "failed",
)
CHUNK_KINDS = ("normal", "parent", "child", "table", "table_summary")
CHUNK_SOURCES = ("auto", "manual")
CHUNK_REVISION_ACTIONS = ("ingest", "edit", "create", "rollback")
DOCUMENT_PARSE_KINDS = ("original", "edited")
METADATA_FIELD_TYPES = ("text", "number", "date", "select")
KB_KINDS = ("doc", "qa")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class KnowledgeBase(Base):
    """知识库。名称、所属组织、可见范围、状态在 resources，本表用同一个 id。"""

    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("resources.id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embedding_model_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("models.id"), nullable=False
    )
    embedding_dim: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    # 每库索引表名，形如 kb_index.kb_{id}（app/kb/index_table.py）。
    index_table: Mapped[str] = mapped_column(sa.Text, nullable=False)
    index_schema_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    retrieval_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    doc_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    chunk_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (sa.CheckConstraint(_in_list("kind", KB_KINDS), name="kind"),)


class KbDefaultScheme(Base):
    """知识库为各文件类型设的默认入库方案（知识库技术方案 14）。"""

    __tablename__ = "kb_default_schemes"

    kb_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True
    )
    file_type: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    # RESTRICT：被设为默认的方案删不掉，由数据库保证（需求 4.1.4）。
    scheme_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("ingest_schemes.id", ondelete="RESTRICT"), nullable=False
    )


class KbFolder(Base):
    """知识库文件夹。path 是物化路径，如 /1/5/，按前缀匹配含下级。"""

    __tablename__ = "kb_folders"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("kb_folders.id", ondelete="CASCADE"), nullable=True
    )
    path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)

    __table_args__ = (
        sa.Index("uq_kb_folders_kb_path", "kb_id", "path", unique=True),
        sa.Index("uq_kb_folders_kb_parent_name", "kb_id", "parent_id", "name", unique=True),
    )


class KbMetadataField(Base):
    """知识库自定义元数据字段（需求 4.1.3）。"""

    __tablename__ = "kb_metadata_fields"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    options: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        sa.CheckConstraint(_in_list("type", METADATA_FIELD_TYPES), name="type"),
        sa.Index("uq_kb_metadata_fields_kb_name", "kb_id", "name", unique=True),
    )


class Document(Base):
    """文档（需求 4.1.3、知识库技术方案 8.1、11.2）。"""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    folder_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("kb_folders.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    ext: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    step: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    progress: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    failed_step: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    queued_at: Mapped[sa.DateTime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    started_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # 本次入库的 run_id，重新解析 / 重试时换新值，旧任务据此退出（知识库技术方案 8.3）。
    run_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    scheme_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("ingest_schemes.id", ondelete="SET NULL"), nullable=True
    )
    parse_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chunk_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    current_generation: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    # 列名是 metadata，Python 属性不能叫 metadata（Base.metadata 保留），故用 doc_metadata。
    doc_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    page_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    enhance_partial_failed: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    uploaded_by: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    # 发起本次入库的人（上传、重新解析或重试），用量记在他名下（技术方案 6.3）。
    run_by: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.CheckConstraint(_in_list("file_type", FILE_TYPES), name="file_type"),
        sa.CheckConstraint(_in_list("status", DOCUMENT_STATUSES), name="status"),
        # 同名只在同一文件夹内判断；folder_id 为空表示未分类，用 NULLS NOT DISTINCT 让它也算同名。
        sa.Index(
            "uq_documents_kb_folder_name",
            "kb_id",
            "folder_id",
            "name",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        sa.Index("ix_documents_kb_sha256", "kb_id", "sha256"),
        sa.Index("ix_documents_kb_status_queued_at", "kb_id", "status", "queued_at"),
        sa.Index("ix_documents_metadata", "metadata", postgresql_using="gin"),
    )


class DocumentParse(Base):
    """文档解析结果，每个文档最多两行（original / edited）。"""

    __tablename__ = "document_parses"

    doc_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    doc_key: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    md_key: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    engine: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    engine_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    pages: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    ocr_pages: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_by: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (sa.CheckConstraint(_in_list("kind", DOCUMENT_PARSE_KINDS), name="kind"),)


class Chunk(Base):
    """分块。generation 区分新旧两代，parent_id 表示父子分块层级（知识库技术方案 8.4、9.3）。"""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    doc_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("chunks.id", ondelete="CASCADE"), nullable=True
    )
    title_path: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    positions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    edited: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    enhancements: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.CheckConstraint(_in_list("kind", CHUNK_KINDS), name="kind"),
        sa.CheckConstraint(_in_list("source", CHUNK_SOURCES), name="source"),
        sa.Index("ix_chunks_doc_generation_seq", "doc_id", "generation", "seq"),
        sa.Index("ix_chunks_parent_id", "parent_id"),
    )


class ChunkRevision(Base):
    """分块修订历史，回滚即生成新版本（需求 4.1.5）。"""

    __tablename__ = "chunk_revisions"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    chunk_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    title_path: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    editor: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.CheckConstraint(_in_list("action", CHUNK_REVISION_ACTIONS), name="action"),
        sa.Index("ix_chunk_revisions_chunk_version", "chunk_id", "version"),
    )


class IngestScheme(Base):
    """入库方案。内置方案 builtin=true 且不登记资源；自定义方案登记在 resources。"""

    __tablename__ = "ingest_schemes"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    builtin: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    resource_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("resources.id", ondelete="CASCADE"), nullable=True
    )
    file_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        sa.CheckConstraint(_in_list("file_type", FILE_TYPES), name="file_type"),
        sa.Index("uq_ingest_schemes_resource_name", "resource_id", "name", unique=True),
        sa.Index("uq_ingest_schemes_file_type_name", "file_type", "name", unique=True),
    )


class ParseRecord(Base):
    """每次解析一条记录，云端引擎的页数统计从这里出（需求 4.1.11）。"""

    __tablename__ = "parse_records"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(sa.Text, nullable=False)
    pages: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    ocr_pages: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (sa.Index("ix_parse_records_doc_created", "doc_id", "created_at"),)
