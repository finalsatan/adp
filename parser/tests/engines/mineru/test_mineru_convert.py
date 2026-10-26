"""MinerU 适配器的快照测试：用真实 MinerU 输出（samples/）核对转换结果。

覆盖知识库技术方案 19.3 的 MinerU 适配器要点：标题级别、合并单元格表格、图片、
公式、PDF 坐标换算。
"""

from __future__ import annotations

import json

import pytest
from docling_core.types.doc.common.content_layer import ContentLayer
from docling_core.types.doc.labels import DocItemLabel

from app.engines.mineru.convert import parse_table_html, to_document
from tests.engines.mineru.sample_data import find_block, load_sample

PDF_WIDTH = 595.2755737304688
PDF_HEIGHT = 841.8897705078125


@pytest.fixture(scope="module")
def pdf():
    return load_sample("pdf")


@pytest.fixture(scope="module")
def docx():
    return load_sample("docx")


@pytest.fixture(scope="module")
def pptx():
    return load_sample("pptx")


def convert(sample):
    return to_document(sample.middle_json, images=sample.images, name=sample.kind)


def texts(document) -> list:
    return list(document.texts)


def find_text(document, needle: str):
    for item in document.texts:
        if needle in item.text:
            return item
    raise AssertionError(f"没有找到包含 {needle!r} 的元素")


# ---- PDF：页码、坐标、公式、图片、合并单元格表格 ----
def test_pdf_pages_and_sizes(pdf):
    converted = convert(pdf)
    assert converted.pages == 2
    assert converted.ocr_pages == 0
    assert sorted(converted.document.pages) == [1, 2]
    for page in converted.document.pages.values():
        assert page.size.width == pytest.approx(PDF_WIDTH)
        assert page.size.height == pytest.approx(PDF_HEIGHT)


def test_pdf_heading_level_and_coordinates(pdf):
    """页码 = page_idx + 1；坐标换算成 PDF 坐标系（原点左下，单位点）。"""
    converted = convert(pdf)
    block = find_block(pdf, "paragraph_title")
    assert block["level"] == 2  # 实测：文档里最高一级标题输出为 2 级
    heading = find_text(converted.document, "MinerU Sample Report")
    assert heading.label == DocItemLabel.SECTION_HEADER
    assert heading.text == "MinerU Sample Report"

    x0, y0, x1, y1 = block["bbox"]
    prov = heading.prov[0]
    assert prov.page_no == 1
    assert prov.bbox.l == pytest.approx(x0 * PDF_WIDTH)
    assert prov.bbox.r == pytest.approx(x1 * PDF_WIDTH)
    # 归一化坐标原点在左上，PDF 坐标原点在左下
    assert prov.bbox.t == pytest.approx((1 - y0) * PDF_HEIGHT)
    assert prov.bbox.b == pytest.approx((1 - y1) * PDF_HEIGHT)
    # 标题在页面上部：top 靠近页高，且 top > bottom
    assert prov.bbox.t > PDF_HEIGHT * 0.8
    assert prov.bbox.t > prov.bbox.b


def test_pdf_second_page_coordinates(pdf):
    converted = convert(pdf)
    item = find_text(converted.document, "Text on the second page")
    assert item.prov[0].page_no == 2
    assert item.prov[0].bbox.t > item.prov[0].bbox.b


def test_pdf_heading_level_normalized_to_one(pdf):
    """文档里最高一级标题（这里是 2 级）变成 1 级。"""
    converted = convert(pdf)
    heading = find_text(converted.document, "MinerU Sample Report")
    assert heading.level == 1
    section = find_text(converted.document, "Section Two")
    assert section.level == 1


def test_pdf_formula_saved_as_text(pdf):
    converted = convert(pdf)
    block = find_block(pdf, "equation")
    formula = find_text(converted.document, block["content"])
    assert formula.label == DocItemLabel.FORMULA
    assert formula.text == block["content"]
    assert formula.prov[0].page_no == 1


def test_pdf_image_from_zip(pdf):
    converted = convert(pdf)
    block = find_block(pdf, "image")
    image_path = block["content"][0]["image_path"]
    assert image_path in pdf.images
    assert len(converted.document.pictures) == 1
    picture = converted.document.pictures[0]
    assert picture.image is not None
    assert picture.prov[0].page_no == 1
    x0, y0, x1, y1 = block["bbox"]
    assert picture.prov[0].bbox.l == pytest.approx(x0 * PDF_WIDTH)
    assert picture.prov[0].bbox.b == pytest.approx((1 - y1) * PDF_HEIGHT)


def test_pdf_table_with_merged_cell(pdf):
    converted = convert(pdf)
    assert len(converted.document.tables) == 1
    table = converted.document.tables[0]
    assert (table.data.num_rows, table.data.num_cols) == (4, 3)
    # 表头行被识别成一个跨 3 列的单元格（MinerU 实际输出）
    merged = [cell for cell in table.data.table_cells if cell.col_span > 1]
    assert [
        (cell.start_row_offset_idx, cell.start_col_offset_idx, cell.col_span) for cell in merged
    ] == [(0, 0, 3)]
    assert table.prov[0].page_no == 1


# ---- docx：标题层级、合并单元格、图片、没有 bbox ----
def test_docx_headings_normalized(docx):
    """实测 docx 的一级标题输出为 2 级，映射后最高一级是 1 级。"""
    converted = convert(docx)
    levels = {
        item.text: getattr(item, "level", None)
        for item in converted.document.texts
        if getattr(item, "level", None)
    }
    assert levels == {
        "季度经营分析报告": 1,
        "一、收入情况": 2,
        "1.1 分区域明细": 3,
    }


def test_docx_has_no_page_items_and_no_bbox(docx):
    """原生解析的 docx 没有页面尺寸，坐标为空。"""
    converted = convert(docx)
    assert converted.pages == 1
    assert converted.document.pages == {}
    for item in converted.document.texts:
        assert item.prov[0].page_no == 1
        assert item.prov[0].bbox.l == 0


def test_docx_table_merge(docx):
    converted = convert(docx)
    table = converted.document.tables[0]
    assert (table.data.num_rows, table.data.num_cols) == (3, 3)
    cells = {
        (cell.start_row_offset_idx, cell.start_col_offset_idx): cell
        for cell in table.data.table_cells
    }
    assert cells[(0, 0)].text == "区域"
    assert cells[(0, 0)].column_header is True
    assert cells[(2, 0)].text == "合计"
    assert cells[(2, 0)].col_span == 2
    # 被合并覆盖的位置没有单元格
    assert (2, 1) not in cells
    assert cells[(2, 2)].text == "1200"


def test_docx_image(docx):
    converted = convert(docx)
    block = find_block(docx, "image")
    assert block["content"][0]["image_path"] in docx.images
    assert len(converted.document.pictures) == 1
    assert converted.document.pictures[0].image.size.width == pytest.approx(240)
    assert converted.document.pictures[0].image.size.height == pytest.approx(150)


def test_docx_paragraph_text(docx):
    converted = convert(docx)
    paragraph = find_text(converted.document, "本报告汇总了本季度的经营数据")
    assert paragraph.label == DocItemLabel.TEXT


# ---- pptx：doc_title、列表、图片、每页一个页码 ----
def test_pptx_titles_and_pages(pptx):
    converted = convert(pptx)
    assert converted.pages == 2
    title = find_text(converted.document, "智能体开发平台介绍")
    assert title.level == 1  # doc_title 就是 1 级，不参与下移
    assert title.prov[0].page_no == 1
    section = find_text(converted.document, "一、解析引擎")
    assert section.level == 2
    assert section.prov[0].page_no == 2


def test_pptx_list_items(pptx):
    converted = convert(pptx)
    items = [item for item in converted.document.texts if item.label == DocItemLabel.LIST_ITEM]
    assert [item.text for item in items] == [
        "Docling：PDF 与 Office",
        "MinerU：版面与公式还原",
        "内置解析：Excel 与文本",
    ]
    # 列表项开头的“-”不重复保留
    assert all(not item.text.startswith("-") for item in items)


def test_pptx_image(pptx):
    converted = convert(pptx)
    assert len(converted.document.pictures) == 1
    assert converted.document.pictures[0].prov[0].page_no == 2


# ---- 表格 HTML 解析 ----
def test_parse_table_html_with_rowspan_and_colspan():
    html = (
        "<table><thead><tr><th>名称</th><th>值</th></tr></thead><tbody>"
        '<tr><td rowspan="2">A</td><td>1</td></tr>'
        '<tr><td colspan="2">合计</td></tr>'
        "</tbody></table>"
    )
    rows, merges, header_rows = parse_table_html(html)
    assert header_rows == 1
    # 有 rowspan 时网格是 3 列，被合并覆盖的位置是空串
    assert rows == [["名称", "值", ""], ["A", "1", ""], ["", "合计", ""]]
    assert merges == [(1, 0, 2, 1), (2, 1, 1, 2)]


def test_parse_table_html_without_thead_defaults_to_one_header_row():
    rows, merges, header_rows = parse_table_html("<table><tr><td>a</td></tr></table>")
    assert rows == [["a"]]
    assert merges == []
    assert header_rows == 1


def test_parse_table_html_strips_inner_tags():
    rows, _, _ = parse_table_html("<table><tr><td><p>第一行</p><p>第二行</p></td></tr></table>")
    assert rows == [["第一行\n第二行"]]


def test_parse_table_html_empty():
    assert parse_table_html("") == ([], [], 1)
    assert parse_table_html("not html") == ([], [], 1)


# ---- 其它块类型 ----
def test_chart_block_becomes_picture():
    """图片被版面模型判成 chart 时同样按图片保存（image 文件在 images/ 里）。"""
    middle = {
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "chart",
                        "index": 0,
                        "bbox": [0.0, 0.0, 1.0, 1.0],
                        "content": [
                            {
                                "type": "chart_body",
                                "index": 0,
                                "image_path": "images/chart.jpg",
                                "content": "",
                            }
                        ],
                    }
                ],
            }
        ],
        "extensions": {
            "docvortex_layout": {
                "version": 1,
                "pages": [{"page_idx": 0, "width_pt": 100, "height_pt": 200}],
            }
        },
    }
    png = _tiny_png()
    converted = to_document(middle, images={"images/chart.jpg": png}, name="chart")
    assert len(converted.document.pictures) == 1
    prov = converted.document.pictures[0].prov[0]
    assert (prov.bbox.l, prov.bbox.t, prov.bbox.r, prov.bbox.b) == (0.0, 200.0, 100.0, 0.0)


def test_header_footer_become_furniture():
    middle = {
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "header",
                        "index": 0,
                        "content": [{"type": "text", "content": "页眉"}],
                    },
                    {
                        "type": "footer",
                        "index": 1,
                        "content": [{"type": "text", "content": "页脚"}],
                    },
                    {
                        "type": "page_number",
                        "index": 2,
                        "content": [{"type": "text", "content": "1"}],
                    },
                ],
            }
        ]
    }
    converted = to_document(middle, name="header")
    layers = {item.text: item.content_layer for item in converted.document.texts}
    labels = {item.text: item.label for item in converted.document.texts}
    assert labels["页眉"] == DocItemLabel.PAGE_HEADER
    assert labels["页脚"] == DocItemLabel.PAGE_FOOTER
    assert labels["1"] == DocItemLabel.PAGE_FOOTER
    assert all(layer == ContentLayer.FURNITURE for layer in layers.values())


def test_unknown_block_type_keeps_text():
    middle = {
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "future_type",
                        "index": 0,
                        "content": [{"type": "text", "content": "新类型"}],
                    }
                ],
            }
        ]
    }
    converted = to_document(middle, name="future")
    assert [item.text for item in converted.document.texts] == ["新类型"]


def test_document_without_layout_extension_has_no_bbox():
    middle = {"pages": [{"page_idx": 0, "blocks": [{"type": "text", "index": 0, "content": "x"}]}]}
    converted = to_document(middle, name="plain")
    assert converted.document.pages == {}
    assert converted.document.texts[0].prov[0].bbox.l == 0


def test_middle_json_is_json_serializable(pdf):
    """转换结果能导出成 doc.json（X03 的导出路径）。"""
    converted = convert(pdf)
    payload = json.loads(converted.document.model_dump_json())
    assert payload["name"] == "pdf"
    assert len(payload["texts"]) == len(converted.document.texts)


def _tiny_png() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, format="PNG")
    return buffer.getvalue()
