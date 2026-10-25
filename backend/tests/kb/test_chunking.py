"""分块：元素流、位置映射、五种分块方式、表格、超长检查、写库（P3-10）。

用 `backend/tests/kb/fixtures/` 下固定的 DoclingDocument JSON 样例做快照测试，
不依赖完整 Docling（只装 docling-core）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from docling_core.types.doc.document import DoclingDocument
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ChunkTooLongError
from app.kb.chunking import (
    ChunkData,
    chunk_document,
    ensure_within_limit,
    find_overlong,
    write_chunks,
)
from app.kb.schemas import SchemeConfig
from app.kb.tables import Chunk
from tests.worker import make_document, make_kb

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> DoclingDocument:
    return DoclingDocument.load_from_json(FIXTURES / f"{name}.json")


def make_config(
    file_type: str,
    chunk: dict[str, Any],
    table: dict[str, Any] | None = None,
) -> SchemeConfig:
    data: dict[str, Any] = {
        "schema_version": 1,
        "file_type": file_type,
        "parse": {"engine": "builtin"},
        "chunk": chunk,
    }
    if table is not None:
        data["table"] = table
    return SchemeConfig.model_validate(data)


def snapshot(chunks: list[ChunkData]) -> list[tuple[str, str | None, str]]:
    return [(chunk.kind, chunk.title_path, chunk.content) for chunk in chunks]


# ---- 五种分块方式快照（知识库技术方案 9.2、19.3）----

PDF_MODE = {"max_tokens": 60, "min_tokens": 10, "overlap": 10}
HEADING1 = {"mode": "heading", "heading_level": 1, "max_tokens": 60, "min_tokens": 10}
HEADING2 = {"mode": "heading", "heading_level": 2, "max_tokens": 60, "min_tokens": 10}

EXPECTED_HEADING2 = [
    (
        "normal",
        "第一章 总则",
        "本制度适用于公司全体员工，自发布之日起执行。\n\n"
        "员工应当遵守公司各项规章制度，如有违反按相关规定处理。",
    ),
    (
        "table",
        "第一章 总则",
        "| 项目   | 标准       |\n|--------|------------|\n"
        "| 交通   | 高铁二等座 |\n| 住宿   | 400元/晚   |",
    ),
    (
        "normal",
        "第二章 报销 > 2.1 差旅报销",
        "图 1 报销流程\n\n出差前需在系统中提交申请，经直属上级批准后方可预订交通和住宿。",
    ),
    (
        "normal",
        "第二章 报销 > 2.1 差旅报销",
        "市内交通凭票报销，跨城交通优先选择高铁二等座。\n\n"
        "住宿费按城市级别设置上限，超标部分需要部门负责人审批。",
    ),
    ("normal", "第二章 报销 > 2.2 招待报销", "招待费用需提前申请，并注明招待对象和事由。"),
]

EXPECTED_HEADING1 = [
    EXPECTED_HEADING2[0],
    EXPECTED_HEADING2[1],
    (
        "normal",
        "第二章 报销",
        "图 1 报销流程\n\n出差前需在系统中提交申请，经直属上级批准后方可预订交通和住宿。",
    ),
    (
        "normal",
        "第二章 报销",
        "市内交通凭票报销，跨城交通优先选择高铁二等座。\n\n"
        "住宿费按城市级别设置上限，超标部分需要部门负责人审批。",
    ),
    ("normal", "第二章 报销", "招待费用需提前申请，并注明招待对象和事由。"),
]

EXPECTED_LENGTH = [
    EXPECTED_HEADING2[0],
    EXPECTED_HEADING2[1],
    (
        "normal",
        "第一章 总则",
        "图 1 报销流程\n\n出差前需在系统中提交申请，经直属上级批准后方可预订交通和住宿。",
    ),
    (
        "normal",
        "第二章 报销 > 2.1 差旅报销 > 2.1.1 交通标准",
        "市内交通凭票报销，跨城交通优先选择高铁二等座。\n\n"
        "住宿费按城市级别设置上限，超标部分需要部门负责人审批。",
    ),
    ("normal", "第二章 报销 > 2.2 招待报销", "招待费用需提前申请，并注明招待对象和事由。"),
]

EXPECTED_PAGE = [
    EXPECTED_HEADING2[0],
    EXPECTED_HEADING2[1],
    ("normal", "第一章 总则", "图 1 报销流程"),
    (
        "normal",
        "第二章 报销 > 2.1 差旅报销 > 2.1.1 交通标准",
        "出差前需在系统中提交申请，经直属上级批准后方可预订交通和住宿。",
    ),
    (
        "normal",
        "第二章 报销 > 2.1 差旅报销 > 2.1.1 交通标准",
        "市内交通凭票报销，跨城交通优先选择高铁二等座。\n\n"
        "住宿费按城市级别设置上限，超标部分需要部门负责人审批。",
    ),
    ("normal", "第二章 报销 > 2.2 招待报销", "招待费用需提前申请，并注明招待对象和事由。"),
]

EXPECTED_WHOLE = [
    EXPECTED_HEADING2[0],
    EXPECTED_HEADING2[1],
    (
        "normal",
        "第一章 总则",
        "图 1 报销流程\n\n出差前需在系统中提交申请，经直属上级批准后方可预订交通和住宿。\n\n"
        "市内交通凭票报销，跨城交通优先选择高铁二等座。\n\n"
        "住宿费按城市级别设置上限，超标部分需要部门负责人审批。\n\n"
        "招待费用需提前申请，并注明招待对象和事由。",
    ),
]


def test_auto_uses_heading_when_headings_present() -> None:
    auto = chunk_document(load("pdf"), make_config("pdf", {"mode": "auto", **PDF_MODE}))
    heading = chunk_document(
        load("pdf"),
        make_config("pdf", HEADING2),
    )
    assert snapshot(auto) == snapshot(heading) == EXPECTED_HEADING2


def test_heading_mode_snapshot() -> None:
    chunks = chunk_document(
        load("pdf"),
        make_config("pdf", HEADING2),
    )
    assert snapshot(chunks) == EXPECTED_HEADING2
    assert [chunk.seq for chunk in chunks] == list(range(5))

    level_one = chunk_document(
        load("pdf"),
        make_config("pdf", HEADING1),
    )
    assert snapshot(level_one) == EXPECTED_HEADING1


def test_length_mode_snapshot() -> None:
    chunks = chunk_document(
        load("pdf"),
        make_config("pdf", {"mode": "length", "max_tokens": 60, "overlap": 10}),
    )
    assert snapshot(chunks) == EXPECTED_LENGTH


def test_page_mode_snapshot() -> None:
    chunks = chunk_document(
        load("pdf"),
        make_config("pdf", {"mode": "page", "pages_per_chunk": 1, "max_tokens": 60}),
    )
    assert snapshot(chunks) == EXPECTED_PAGE


def test_whole_mode_snapshot() -> None:
    chunks = chunk_document(load("pdf"), make_config("pdf", {"mode": "whole", "max_tokens": 1000}))
    assert snapshot(chunks) == EXPECTED_WHOLE


def test_whole_mode_falls_back_to_length() -> None:
    chunks = chunk_document(load("pdf"), make_config("pdf", {"mode": "whole", "max_tokens": 60}))
    assert len(chunks) > len(EXPECTED_WHOLE)
    assert all(chunk.token_count <= 60 for chunk in chunks)


def test_auto_falls_back_to_page_for_ppt() -> None:
    chunks = chunk_document(
        load("ppt"), make_config("ppt", {"mode": "auto", "max_tokens": 60, "min_tokens": 10})
    )
    assert [(chunk.title_path, chunk.positions) for chunk in chunks] == [
        (None, [{"page": 1}]),
        (None, [{"page": 2}]),
    ]
    assert chunks[0].content == "第一页介绍产品定位和目标用户。\n\n第一页还列出了本期要解决的问题。"


def test_auto_falls_back_to_length_without_headings_or_pages() -> None:
    chunks = chunk_document(
        load("text"),
        make_config("txt", {"mode": "auto", "max_tokens": 30, "min_tokens": 10, "overlap": 5}),
    )
    assert [chunk.content for chunk in chunks] == [
        "为了规范报销流程，现将有关事项通知如下。",
        "员工应在费用发生后一个月内提交报销申请。",
        "超过期限的申请需要部门负责人额外审批。",
        "本通知自发布之日起执行。",
    ]


def test_heading_splits_single_overlong_element() -> None:
    """单个元素仍超长时交给 RecursiveCharacterTextSplitter（9.2）。"""
    chunks = chunk_document(
        load("text"),
        make_config(
            "txt", {"mode": "heading", "heading_level": 2, "max_tokens": 15, "min_tokens": 0}
        ),
    )
    assert len(chunks) == 7  # 每段超过 15 tokens，按“。”再切一次
    assert chunks[0].positions == [{"ref": "#/texts/0"}]
    assert chunks[1].positions == [{"ref": "#/texts/0"}]
    assert {chunk.title_path for chunk in chunks} == {None}


def test_page_mode_groups_pages() -> None:
    chunks = chunk_document(
        load("pdf"), make_config("pdf", {"mode": "page", "pages_per_chunk": 2, "max_tokens": 60})
    )
    pages = {position["page"] for chunk in chunks for position in chunk.positions}
    assert pages == {1, 2}
    # 每两页一块：page 1 的两段文本合成一块，caption 单独一块，表格单独一块。
    assert chunks[0].content == (
        "本制度适用于公司全体员工，自发布之日起执行。\n\n"
        "员工应当遵守公司各项规章制度，如有违反按相关规定处理。"
    )


# ---- 位置映射（知识库技术方案 9.1、13）----

EXPECTED_PDF_POSITIONS = {
    (1, (72.0, 740.0, 523.0, 700.0), "BOTTOMLEFT"),
    (1, (72.0, 690.0, 523.0, 650.0), "BOTTOMLEFT"),
    (1, (72.0, 620.0, 523.0, 520.0), "BOTTOMLEFT"),
    (1, (72.0, 500.0, 272.0, 460.0), "BOTTOMLEFT"),
    (2, (72.0, 680.0, 523.0, 640.0), "BOTTOMLEFT"),
    (2, (72.0, 630.0, 523.0, 590.0), "BOTTOMLEFT"),
    (2, (72.0, 520.0, 523.0, 480.0), "BOTTOMLEFT"),
    (2, (72.0, 400.0, 523.0, 360.0), "BOTTOMLEFT"),
}

POSITION_MODES = [
    {"mode": "auto", "max_tokens": 60, "min_tokens": 10, "overlap": 10},
    {"mode": "heading", "heading_level": 2, "max_tokens": 60, "min_tokens": 10},
    {"mode": "length", "max_tokens": 60, "overlap": 10},
    {"mode": "page", "pages_per_chunk": 1, "max_tokens": 1000},
    {"mode": "whole", "max_tokens": 1000},
]


@pytest.mark.parametrize("chunk_config", POSITION_MODES)
def test_pdf_positions_cover_source_elements(chunk_config: dict[str, Any]) -> None:
    """同一文档用不同方式切分，位置映射都覆盖且只覆盖源元素（证明共用同一映射）。"""
    chunks = chunk_document(load("pdf"), make_config("pdf", chunk_config))
    covered = {
        (position["page"], tuple(position["bbox"]), position["origin"])
        for chunk in chunks
        for position in chunk.positions
    }
    assert covered == EXPECTED_PDF_POSITIONS


def test_word_positions_are_element_refs() -> None:
    chunks = chunk_document(
        load("word"),
        make_config("word", HEADING1),
    )
    assert [chunk.positions for chunk in chunks] == [
        [{"ref": "#/texts/2"}, {"ref": "#/texts/3"}],
        [{"ref": "#/texts/5"}, {"ref": "#/texts/6"}],
    ]
    assert [chunk.title_path for chunk in chunks] == ["第一章 入职", "第二章 考勤"]


def test_picture_caption_keeps_its_position() -> None:
    chunks = chunk_document(
        load("pdf"), make_config("pdf", {"mode": "page", "pages_per_chunk": 1, "max_tokens": 60})
    )
    caption = next(chunk for chunk in chunks if chunk.content == "图 1 报销流程")
    assert caption.positions == [
        {"page": 1, "bbox": [72.0, 500.0, 272.0, 460.0], "origin": "BOTTOMLEFT"}
    ]


# ---- 表格（知识库技术方案 9.3）----

TABLE_HEADER = "|   序号 | 项目   |   金额 | 备注   |\n|--------|--------|--------|--------|\n"


def test_document_table_is_one_chunk_when_short() -> None:
    chunks = chunk_document(
        load("pdf"), make_config("pdf", {"mode": "auto", "max_tokens": 500, "min_tokens": 10})
    )
    tables = [chunk for chunk in chunks if chunk.kind == "table"]
    assert len(tables) == 1
    assert tables[0].content.startswith("| 项目   | 标准")


def test_overlong_table_splits_by_rows_with_header() -> None:
    chunks = chunk_document(
        load("table"), make_config("pdf", {"mode": "auto", "max_tokens": 120, "min_tokens": 10})
    )
    assert all(chunk.kind == "table" for chunk in chunks)
    assert len(chunks) == 5
    assert all(chunk.content.startswith(TABLE_HEADER) for chunk in chunks)
    # 每块都带表头，且数据行不重复。
    rows = [
        line
        for chunk in chunks
        for line in chunk.content.splitlines()[2:]  # 前两行是表头和分隔行
    ]
    assert len(rows) == 20
    assert len(set(rows)) == 20
    # PDF 表格的每个子块都带同一张表的页码和坐标。
    assert {chunk.positions[0]["page"] for chunk in chunks} == {1}


def test_excel_chunks_by_rows_with_sheet_and_row_range() -> None:
    chunks = chunk_document(
        load("excel"),
        make_config("excel", {"mode": "auto"}, {"rows_per_chunk": 3}),
    )
    assert [(chunk.positions, chunk.kind) for chunk in chunks] == [
        ([{"sheet": "Sheet1", "rows": [2, 4]}], "table"),
        ([{"sheet": "Sheet1", "rows": [5, 7]}], "table"),
        ([{"sheet": "Sheet2", "rows": [2, 4]}], "table"),
    ]
    for chunk in chunks:
        assert chunk.content.startswith("|")  # 每块都带表头
    assert "| 张三" in chunks[0].content and "| 王五" in chunks[0].content
    assert "| 赵六" in chunks[1].content and "| 周八" in chunks[1].content


def test_excel_ignores_chunk_mode() -> None:
    """Excel、CSV 不使用分块方式，按行分块（9.3）。"""
    by_rows = chunk_document(
        load("excel"),
        make_config("excel", {"mode": "whole", "max_tokens": 1000}, {"rows_per_chunk": 3}),
    )
    assert len(by_rows) == 3


# ---- 超长检查（知识库技术方案 9.4）----


def test_overlong_check_reports_seq_and_page() -> None:
    chunks = chunk_document(
        load("pdf"), make_config("pdf", {"mode": "page", "pages_per_chunk": 1, "max_tokens": 1000})
    )
    result = find_overlong(chunks, embedding_max_input=200, title_path_enabled=True)
    assert result is not None
    assert (result.seq, result.page) == (3, 2)

    with pytest.raises(ChunkTooLongError) as excinfo:
        ensure_within_limit(chunks, embedding_max_input=200, title_path_enabled=True)
    assert excinfo.value.code == "CHUNK_TOO_LONG"
    assert "第 3 个分块" in str(excinfo.value)
    assert "第 2 页" in str(excinfo.value)


def test_overlong_check_passes_when_within_limit() -> None:
    chunks = chunk_document(load("pdf"), make_config("pdf", {"mode": "whole", "max_tokens": 1000}))
    assert find_overlong(chunks, embedding_max_input=100000, title_path_enabled=True) is None
    ensure_within_limit(chunks, embedding_max_input=100000, title_path_enabled=True)


def test_overlong_check_counts_title_path_and_reserved_context() -> None:
    chunks = chunk_document(
        load("word"), make_config("word", {"mode": "whole", "max_tokens": 1000})
    )
    assert len(chunks) == 1
    # 把上限卡在“内容 + 预留上下文”刚好通过的位置：不带标题路径通过，带标题路径命中。
    limit = int((chunks[0].token_count + 100) / 0.9) + 1
    assert find_overlong(chunks, embedding_max_input=limit, title_path_enabled=False) is None
    assert find_overlong(chunks, embedding_max_input=limit, title_path_enabled=True) is not None


# ---- 写库（知识库技术方案 11.2）----


async def test_write_chunks_fields(session: AsyncSession) -> None:
    kb, user_id = await make_kb(session, "分块库")
    doc_id = await make_document(session, kb.id, user_id, name="制度.pdf")
    chunks = chunk_document(load("pdf"), make_config("pdf", HEADING2))
    await write_chunks(
        session,
        kb_id=kb.id,
        doc_id=doc_id,
        generation=2,
        chunks=chunks,
        run_by=user_id,
    )
    await session.commit()

    rows = (
        (await session.execute(select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.seq)))
        .scalars()
        .all()
    )
    assert [row.seq for row in rows] == list(range(len(chunks)))
    first = rows[0]
    assert first.kb_id == kb.id
    assert first.generation == 2
    assert first.kind == "normal"
    assert first.title_path == "第一章 总则"
    assert first.content == chunks[0].content
    assert first.token_count == chunks[0].token_count
    assert first.positions == chunks[0].positions
    assert first.source == "auto"
    assert first.enabled is True
    assert first.edited is False
    assert first.version == 1
    assert first.updated_by == user_id
    assert rows[1].kind == "table"
