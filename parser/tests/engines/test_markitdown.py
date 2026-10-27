"""MarkItDown 引擎的测试（X07）。

样例：X02 的 ``tests/samples/sample.docx``（X06 与本卡共用），以及
``office_samples.py`` 现场生成的带多级标题的 docx、带表格的多页 pptx。
不往仓库提交二进制。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from docling_core.types.doc.labels import DocItemLabel

from app.engines.base import ParseContext
from app.engines.markitdown_engine import MarkitdownEngine, split_slides
from app.engines.registry import bootstrap, get_engine
from app.errors import ParseError
from app.filetypes import FILE_TYPES
from tests.engines import office_samples

ENGINE = MarkitdownEngine()

# X02 生成的 Word 样例（只有一段正文，没有标题和表格）
X02_DOCX = Path(__file__).resolve().parent.parent / "samples" / "sample.docx"
X02_DOCX_TEXT = "智能体开发平台中文预览测试"

HEADING_LABELS = (DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER)


def make_ctx(
    path: Path,
    file_type: str,
    ext: str,
    reports: list | None = None,
) -> ParseContext:
    return ParseContext(
        input_path=str(path),
        file_type=file_type,
        ext=ext,
        engine_params={},
        preprocess={},
        table={},
        work_dir=str(path.parent),
        report=(
            (lambda done, total: reports.append((done, total)))
            if reports is not None
            else (lambda done, total: None)
        ),
    )


def headings(document) -> list[tuple[DocItemLabel, int | None, str]]:
    """按文档顺序列出标题（含文档标题）。"""
    return [
        (item.label, getattr(item, "level", None), item.text)
        for item in document.texts
        if item.label in HEADING_LABELS
    ]


def body_texts(document) -> list[str]:
    return [item.text for item in document.texts if item.label == DocItemLabel.TEXT]


def items_with_pages(document) -> list[tuple[int | None, DocItemLabel, str | None]]:
    """按文档顺序列出每个元素及其页码（没有页码时为 None）。"""
    return [
        (
            item.prov[0].page_no if getattr(item, "prov", None) else None,
            item.label,
            getattr(item, "text", None),
        )
        for item, _level in document.iterate_items()
        if hasattr(item, "label")
    ]


def test_registry_registers_markitdown():
    bootstrap()
    engine = get_engine("markitdown")
    assert engine is not None
    assert engine.availability() == (True, None)
    assert engine.supports("word", "docx")
    assert engine.supports("ppt", "pptx")
    assert not engine.supports("pdf", "pdf")
    assert not engine.supports("markdown", "md")

    # /v1/engines 用每种类型的第一个扩展名探测支持的类型（main.py），所以 doc、ppt
    # 这些旧格式也要在类型层面支持，实际解析时再返回 UNSUPPORTED_TYPE
    supported = [ft for ft, exts in FILE_TYPES.items() if engine.supports(ft, exts[0])]
    assert supported == ["word", "ppt"]


# ---- Word ----


def test_docx_sample_from_x02(tmp_path):
    """X02 的样例：只有一段正文，没有标题、表格，也没有页码。"""
    output = ENGINE.parse(make_ctx(X02_DOCX, "word", "docx"))

    document = output.document
    assert body_texts(document) == [X02_DOCX_TEXT]
    assert headings(document) == []
    assert document.tables == []
    assert output.pages == 0
    assert output.ocr_pages == 0
    assert output.engine_version.startswith("markitdown")
    # Word 没有页码，元素不设 prov（需求 4.1.8）
    assert all(item.prov == [] for item in document.texts)


def test_docx_headings_and_table_snapshot(tmp_path):
    path = office_samples.write_docx(tmp_path / "headings.docx")
    reports: list[tuple[int, int]] = []
    output = ENGINE.parse(make_ctx(path, "word", "docx", reports))

    document = output.document
    assert reports == [(1, 1)]
    assert output.pages == 0
    assert document.pages == {}

    # MarkItDown 把 Word 的一级标题转成 `#`、二级转成 `##`；markdown_doc.py
    # 统一成和 Word 原生解析一致的级别：`#` 是 1 级 section_header（X13）
    assert headings(document) == [
        (DocItemLabel.SECTION_HEADER, 1, "第一章 总则"),
        (DocItemLabel.SECTION_HEADER, 2, "1.1 适用范围"),
        (DocItemLabel.SECTION_HEADER, 3, "1.1.1 例外"),
    ]
    assert body_texts(document) == office_samples.DOCX_PARAGRAPHS
    assert len(document.texts) == 6

    # 表格数正确：mammoth 转出的表格没有表头行，MarkItDown 补了一行空表头
    assert len(document.tables) == 1
    table = document.tables[0]
    assert table.data.num_rows == 4
    assert table.data.num_cols == 2
    assert [[cell.text for cell in row] for row in table.data.grid] == [
        ["", ""],
        office_samples.DOCX_TABLE_HEADER,
        *office_samples.DOCX_TABLE_BODY,
    ]

    # 没有页码的文档里，表格和文字都不设 prov
    assert all(item.prov == [] for item in document.tables)
    assert all(item.prov == [] for item in document.texts)


def test_docx_corrupted_file(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a docx")
    with pytest.raises(ParseError) as excinfo:
        ENGINE.parse(make_ctx(path, "word", "docx"))
    assert excinfo.value.code == "FILE_CORRUPTED"


# ---- PPT ----


def test_pptx_pages_snapshot(tmp_path):
    path = office_samples.write_pptx(tmp_path / "slides.pptx")
    reports: list[tuple[int, int]] = []
    output = ENGINE.parse(make_ctx(path, "ppt", "pptx", reports))

    document = output.document
    assert reports == [(1, 3), (2, 3), (3, 3)]
    assert output.pages == 3
    assert output.engine_version.startswith("markitdown")

    # 每页一个页码，页码来自 MarkItDown 的页标记；页面尺寸取自 pptx
    assert list(document.pages) == [1, 2, 3]
    for page in document.pages.values():
        assert (page.size.width, page.size.height) == (
            office_samples.PPTX_SLIDE_WIDTH * 72,
            office_samples.PPTX_SLIDE_HEIGHT * 72,
        )

    # 每个元素都在正确的页上
    assert items_with_pages(document) == [
        (1, DocItemLabel.SECTION_HEADER, office_samples.PPTX_TITLES[1]),
        (1, DocItemLabel.TEXT, "智能体开发平台 支持知识库问答"),
        (2, DocItemLabel.SECTION_HEADER, office_samples.PPTX_TITLES[2]),
        (2, DocItemLabel.TABLE, None),
        (3, DocItemLabel.SECTION_HEADER, office_samples.PPTX_TITLES[3]),
        (3, DocItemLabel.TEXT, office_samples.PPTX_BODY[3]),
        (3, DocItemLabel.PICTURE, None),
        # MarkItDown 输出的图片说明文字，Docling 作为图片的说明保留
        (3, DocItemLabel.TEXT, "logo.png"),
        (3, DocItemLabel.SECTION_HEADER, "Notes:"),
        (3, DocItemLabel.TEXT, office_samples.PPTX_NOTES),
    ]

    # 幻灯片标题（MarkItDown 输出 `# 标题`）按本卡规则是 1 级标题（X13）
    assert [
        (item.level, item.text)
        for item in document.texts
        if item.label == DocItemLabel.SECTION_HEADER
    ] == [
        (1, office_samples.PPTX_TITLES[1]),
        (1, office_samples.PPTX_TITLES[2]),
        (1, office_samples.PPTX_TITLES[3]),
        # MarkItDown 自己的备注小标题，原来是 `###`，加一后是 3 级
        (3, "Notes:"),
    ]
    # 修正后文档里不再用 title 表示标题
    assert all(item.label != DocItemLabel.TITLE for item in document.texts)

    # 每个元素的页码唯一，且都落在文档页数内
    for item, _level in document.iterate_items():
        if not hasattr(item, "prov"):
            continue
        assert len(item.prov) == 1
        assert 1 <= item.prov[0].page_no <= output.pages
        # Markdown 没有坐标，包围框留空
        bbox = item.prov[0].bbox
        assert (bbox.l, bbox.t, bbox.r, bbox.b) == (0.0, 0.0, 0.0, 0.0)

    # 表格数正确
    assert len(document.tables) == 1
    assert [[cell.text for cell in row] for row in document.tables[0].data.grid] == (
        office_samples.PPTX_TABLE
    )
    assert len(document.pictures) == 1

    # 合并出来的文档结构合法
    document.validate_document()


def test_pptx_corrupted_file(tmp_path):
    path = tmp_path / "broken.pptx"
    path.write_bytes(b"not a pptx")
    with pytest.raises(ParseError) as excinfo:
        ENGINE.parse(make_ctx(path, "ppt", "pptx"))
    assert excinfo.value.code == "FILE_CORRUPTED"


def test_split_slides_uses_page_markers():
    markdown = (
        "\n\n<!-- Slide number: 1 -->\n# 第一页\n正文\n\n<!-- Slide number: 2 -->\n# 第二页\n"
    )
    assert split_slides(markdown) == [(1, "# 第一页\n正文"), (2, "# 第二页")]
    # 没有页标记时不分页
    assert split_slides("# 只有一页\n正文") == []


# ---- 不支持的类型 ----


@pytest.mark.parametrize(
    ("file_type", "ext", "expected"),
    [
        ("word", "doc", "docx"),
        ("word", "rtf", "docx"),
        ("word", "odt", "docx"),
        ("ppt", "ppt", "pptx"),
        ("ppt", "odp", "pptx"),
    ],
)
def test_old_formats_unsupported(tmp_path, file_type, ext, expected):
    """旧格式由 worker 先转换（X02），引擎收到时返回 UNSUPPORTED_TYPE。"""
    path = tmp_path / f"input.{ext}"
    path.write_bytes(b"x")
    with pytest.raises(ParseError) as excinfo:
        ENGINE.parse(make_ctx(path, file_type, ext))
    assert excinfo.value.code == "UNSUPPORTED_TYPE"
    assert excinfo.value.message == f"请先将文件转换为 {expected} 后再解析"


@pytest.mark.parametrize(("file_type", "ext"), [("pdf", "pdf"), ("excel", "xlsx"), ("txt", "txt")])
def test_other_types_unsupported(tmp_path, file_type, ext):
    path = tmp_path / f"input.{ext}"
    path.write_bytes(b"x")
    with pytest.raises(ParseError) as excinfo:
        ENGINE.parse(make_ctx(path, file_type, ext))
    assert excinfo.value.code == "UNSUPPORTED_TYPE"
