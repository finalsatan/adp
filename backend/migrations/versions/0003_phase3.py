"""phase3：知识库业务表（知识库技术方案 11.2）

Revision ID: 0003_phase3
Revises: 0002_phase2
Create Date: 2026-10-25

本平台只做向前迁移，不实现 downgrade。
只建表、索引、约束，不插数据（内置方案在 P3-06）。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_phase3"
down_revision: str | None = "0002_phase2"
branch_labels: str | None = None
depends_on: str | None = None

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


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("embedding_model_id", sa.BigInteger(), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("index_table", sa.Text(), nullable=False),
        sa.Column(
            "index_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "retrieval_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("doc_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in_list("kind", KB_KINDS), name=op.f("ck_knowledge_bases_kind")),
        sa.ForeignKeyConstraint(
            ["embedding_model_id"],
            ["models.id"],
            name=op.f("fk_knowledge_bases_embedding_model_id_models"),
        ),
        sa.ForeignKeyConstraint(
            ["id"],
            ["resources.id"],
            name=op.f("fk_knowledge_bases_id_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_bases")),
    )

    op.create_table(
        "ingest_schemes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=True),
        sa.Column("file_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("file_type", FILE_TYPES), name=op.f("ck_ingest_schemes_file_type")
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resources.id"],
            name=op.f("fk_ingest_schemes_resource_id_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_schemes")),
    )
    op.create_index(
        "uq_ingest_schemes_resource_name",
        "ingest_schemes",
        ["resource_id", "name"],
        unique=True,
    )
    op.create_index(
        "uq_ingest_schemes_file_type_name",
        "ingest_schemes",
        ["file_type", "name"],
        unique=True,
    )

    op.create_table(
        "kb_default_schemes",
        sa.Column("kb_id", sa.BigInteger(), nullable=False),
        sa.Column("file_type", sa.Text(), nullable=False),
        sa.Column("scheme_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_kb_default_schemes_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scheme_id"],
            ["ingest_schemes.id"],
            name=op.f("fk_kb_default_schemes_scheme_id_ingest_schemes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("kb_id", "file_type", name=op.f("pk_kb_default_schemes")),
    )

    op.create_table(
        "kb_folders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kb_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_kb_folders_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["kb_folders.id"],
            name=op.f("fk_kb_folders_parent_id_kb_folders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kb_folders")),
    )
    op.create_index("uq_kb_folders_kb_path", "kb_folders", ["kb_id", "path"], unique=True)
    op.create_index(
        "uq_kb_folders_kb_parent_name",
        "kb_folders",
        ["kb_id", "parent_id", "name"],
        unique=True,
    )

    op.create_table(
        "kb_metadata_fields",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kb_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            _in_list("type", METADATA_FIELD_TYPES), name=op.f("ck_kb_metadata_fields_type")
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_kb_metadata_fields_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kb_metadata_fields")),
    )
    op.create_index(
        "uq_kb_metadata_fields_kb_name", "kb_metadata_fields", ["kb_id", "name"], unique=True
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kb_id", sa.BigInteger(), nullable=False),
        sa.Column("folder_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("file_type", sa.Text(), nullable=False),
        sa.Column("ext", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("progress", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_step", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("scheme_id", sa.BigInteger(), nullable=True),
        sa.Column("parse_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("chunk_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("current_generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "enhance_partial_failed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.BigInteger(), nullable=False),
        sa.Column("run_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in_list("file_type", FILE_TYPES), name=op.f("ck_documents_file_type")),
        sa.CheckConstraint(_in_list("status", DOCUMENT_STATUSES), name=op.f("ck_documents_status")),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["kb_folders.id"],
            name=op.f("fk_documents_folder_id_kb_folders"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_documents_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scheme_id"],
            ["ingest_schemes.id"],
            name=op.f("fk_documents_scheme_id_ingest_schemes"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(
        "uq_documents_kb_folder_name",
        "documents",
        ["kb_id", "folder_id", "name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_documents_kb_sha256", "documents", ["kb_id", "sha256"], unique=False)
    op.create_index(
        "ix_documents_kb_status_queued_at",
        "documents",
        ["kb_id", "status", "queued_at"],
        unique=False,
    )
    op.create_index(
        "ix_documents_metadata", "documents", ["metadata"], unique=False, postgresql_using="gin"
    )

    op.create_table(
        "document_parses",
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("doc_key", sa.Text(), nullable=True),
        sa.Column("md_key", sa.Text(), nullable=True),
        sa.Column("engine", sa.Text(), nullable=True),
        sa.Column("engine_version", sa.Text(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("ocr_pages", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("kind", DOCUMENT_PARSE_KINDS), name=op.f("ck_document_parses_kind")
        ),
        sa.ForeignKeyConstraint(
            ["doc_id"],
            ["documents.id"],
            name=op.f("fk_document_parses_doc_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("doc_id", "kind", name=op.f("pk_document_parses")),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kb_id", sa.BigInteger(), nullable=False),
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("title_path", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("positions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("edited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("enhancements", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in_list("kind", CHUNK_KINDS), name=op.f("ck_chunks_kind")),
        sa.CheckConstraint(_in_list("source", CHUNK_SOURCES), name=op.f("ck_chunks_source")),
        sa.ForeignKeyConstraint(
            ["doc_id"],
            ["documents.id"],
            name=op.f("fk_chunks_doc_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_chunks_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["chunks.id"],
            name=op.f("fk_chunks_parent_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
    )
    op.create_index(
        "ix_chunks_doc_generation_seq", "chunks", ["doc_id", "generation", "seq"], unique=False
    )
    op.create_index("ix_chunks_parent_id", "chunks", ["parent_id"], unique=False)

    op.create_table(
        "chunk_revisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chunk_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title_path", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("editor", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("action", CHUNK_REVISION_ACTIONS), name=op.f("ck_chunk_revisions_action")
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_chunk_revisions_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_revisions")),
    )
    op.create_index(
        "ix_chunk_revisions_chunk_version",
        "chunk_revisions",
        ["chunk_id", "version"],
        unique=False,
    )

    op.create_table(
        "parse_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("ocr_pages", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["doc_id"],
            ["documents.id"],
            name=op.f("fk_parse_records_doc_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parse_records")),
    )
    op.create_index(
        "ix_parse_records_doc_created", "parse_records", ["doc_id", "created_at"], unique=False
    )


def downgrade() -> None:
    raise NotImplementedError("本平台只做向前迁移")
