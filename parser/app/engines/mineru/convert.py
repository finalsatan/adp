"""把 MinerU 的 middle_json（docvortex.middle 2.0）转成 DoclingDocument。

对应知识库技术方案 6.2 的 MinerU 一行：

- 页码 = ``page_idx + 1``；
- 块类型：标题（带级别）、正文、列表、表格（HTML，解析成含合并单元格的表格结构）、
  图片、公式（以文字形式保存原始公式）；
- PDF、OFD 的块带归一化坐标（原点在左上），按 ``extensions.docvortex_layout`` 里的
  ``width_pt``/``height_pt`` 换算成 PDF 坐标系（原点左下，单位点）；
- 标题级别归一：让文档里最高的一级标题变成 1 级（实测 docx 的一级标题输出为 2 级）；
- 图片字节从 ZIP 的 ``images/`` 里取（``image_path``），没有时退回块里的 ``image_base64``。
"""

from __future__ import annotations

import base64
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from ... import doctools

__all__ = ["ConvertedDocument", "parse_table_html", "to_document"]

# 固定版式（PDF、OFD）才有 bbox 和页面尺寸
_LAYOUT_EXTENSION = "docvortex_layout"

# 行内 span 的类型（docvortex.middle 2.0 的 InlineSpan 联合），其它类型的子块不取文字
_INLINE_TYPES = {"text", "equation_inline", "code_inline"}

# 容器块：content 是子块列表，不是行内 span
_CONTAINER_TYPES = {"list", "index", "image", "table", "chart", "code"}

_HEADING_TYPES = {"doc_title": 1, "paragraph_title": 2}

# 列表项开头的序号或项目符号
_MARKERS = (
    re.compile(r"^\s*(\d+)[.、)](?!\d)\s*"),
    re.compile(r"^\s*[（(](\d+)[)）]\s*"),
    re.compile(r"^\s*[-*•·▪]\s+"),
)


@dataclass
class ConvertedDocument:
    """转换结果：文档、页数、OCR 页数、没有文字层也没做 OCR 的页码。"""

    document: DoclingDocument
    pages: int
    ocr_pages: int
    empty_pages: list[int] = field(default_factory=list)


def to_document(
    middle: Mapping[str, Any],
    *,
    images: Mapping[str, bytes] | None = None,
    name: str = "mineru",
) -> ConvertedDocument:
    """把一份 middle_json 转成 DoclingDocument。"""
    converter = _Converter(middle, images or {}, name)
    return converter.convert()


class _Converter:
    def __init__(self, middle: Mapping[str, Any], images: Mapping[str, bytes], name: str) -> None:
        self._middle = middle
        self._images = images
        self._document = doctools.new_document(name)
        self._page_sizes = _page_sizes(middle)
        self._levels = _heading_levels(middle)
        self._shift = (min(self._levels) - 1) if self._levels else 0

    def convert(self) -> ConvertedDocument:
        pages = list(self._middle.get("pages") or [])
        for page in pages:
            page_idx = int(page.get("page_idx") or 0)
            size = self._page_sizes.get(page_idx)
            if size is not None:
                doctools.add_page(self._document, page_idx + 1, size[0], size[1])
        for page in pages:
            self._convert_page(page)

        parse_mode = str(
            ((self._middle.get("extensions") or {}).get("mineru") or {}).get("parse_mode") or ""
        )
        ocr_pages = len(pages) if parse_mode == "ocr" else 0
        return ConvertedDocument(document=self._document, pages=len(pages), ocr_pages=ocr_pages)

    # ---- 页和块 ----
    def _convert_page(self, page: Mapping[str, Any]) -> None:
        page_idx = int(page.get("page_idx") or 0)
        for block in page.get("blocks") or []:
            if isinstance(block, Mapping):
                self._convert_block(block, page_idx)

    def _convert_block(self, block: Mapping[str, Any], page_idx: int) -> None:
        block_type = str(block.get("type") or "")
        page_no = page_idx + 1
        bbox = self._bbox(block, page_idx)
        if block_type in _HEADING_TYPES:
            self._add_heading(block, page_no, bbox)
        elif block_type in ("text", "ref_text", "aside_text", "page_footnote"):
            self._add_paragraph(block, page_no, bbox)
        elif block_type == "header":
            self._add_paragraph(
                block, page_no, bbox, label=DocItemLabel.PAGE_HEADER, furniture=True
            )
        elif block_type in ("footer", "page_number"):
            self._add_paragraph(
                block, page_no, bbox, label=DocItemLabel.PAGE_FOOTER, furniture=True
            )
        elif block_type == "list":
            self._add_list(block, page_no, bbox)
        elif block_type == "index":
            self._add_index(block, page_no, bbox)
        elif block_type == "table":
            self._add_table(block, page_no, bbox)
        elif block_type in ("image", "chart"):
            self._add_picture(block, page_no, bbox)
        elif block_type == "equation":
            self._add_equation(block, page_no, bbox)
        elif block_type == "code":
            self._add_code(block, page_no, bbox)
        else:
            # 未来 MinerU 版本新增的类型：有行内文字就按正文保存，不丢内容
            self._add_paragraph(block, page_no, bbox)

    # ---- 各类块 ----
    def _add_heading(self, block: Mapping[str, Any], page_no: int, bbox: Any) -> None:
        default = _HEADING_TYPES[str(block.get("type"))]
        level = int(block.get("level") or default)
        level = max(1, min(6, level - self._shift))
        text = _inline_text(block.get("content"))
        if text:
            doctools.add_heading(self._document, text, level, page_no=page_no, bbox=bbox)

    def _add_paragraph(
        self,
        block: Mapping[str, Any],
        page_no: int,
        bbox: Any,
        *,
        label: DocItemLabel = DocItemLabel.TEXT,
        furniture: bool = False,
    ) -> None:
        text = _inline_text(block.get("content"))
        if not text:
            return
        item = doctools.add_paragraph(self._document, text, label=label, page_no=page_no, bbox=bbox)
        if furniture:
            _mark_furniture(item)

    def _add_list(self, block: Mapping[str, Any], page_no: int, bbox: Any) -> None:
        group = self._document.add_list_group()
        self._add_list_children(block.get("content"), page_no, bbox, group)

    def _add_list_children(self, children: Any, page_no: int, bbox: Any, group: Any) -> None:
        for child in children or []:
            if not isinstance(child, Mapping):
                continue
            if str(child.get("type")) == "list":
                self._add_list_children(child.get("content"), page_no, bbox, group)
                continue
            text = _inline_text(child.get("content"))
            if not text:
                continue
            marker, enumerated, body = _split_marker(text)
            self._document.add_list_item(
                text=body,
                enumerated=enumerated,
                marker=marker,
                prov=doctools.provenance(page_no, bbox, charspan=(0, len(body))),
                parent=group,
            )

    def _add_index(self, block: Mapping[str, Any], page_no: int, bbox: Any) -> None:
        # 目录：按列表项保存，嵌套的目录项拉平
        self._add_index_children(block.get("content"), page_no, bbox)

    def _add_index_children(self, children: Any, page_no: int, bbox: Any) -> None:
        for child in children or []:
            if not isinstance(child, Mapping):
                continue
            if str(child.get("type")) == "index":
                self._add_index_children(child.get("content"), page_no, bbox)
                continue
            text = _inline_text(child.get("content"))
            if text:
                doctools.add_paragraph(
                    self._document,
                    text,
                    label=DocItemLabel.LIST_ITEM,
                    page_no=page_no,
                    bbox=bbox,
                )

    def _add_table(self, block: Mapping[str, Any], page_no: int, bbox: Any) -> None:
        body = _child_of_type(block, "table_body")
        html = str((body or {}).get("content") or "")
        rows, merges, header_rows = parse_table_html(html)
        caption = _annotation_text(block, "table_caption")
        if not rows:
            # 表格结构没识别出来时只留说明，不把 HTML 当正文
            if caption:
                self._add_caption(caption, page_no, bbox)
            return
        doctools.add_table(
            self._document,
            rows,
            header_rows=header_rows,
            merges=merges,
            page_no=page_no,
            bbox=bbox,
        )
        if caption:
            self._add_caption(caption, page_no, bbox)

    def _add_picture(self, block: Mapping[str, Any], page_no: int, bbox: Any) -> None:
        body = _child_of_type(block, "image_body") or _child_of_type(block, "chart_body")
        caption = _annotation_text(block, "image_caption") or _annotation_text(
            block, "chart_caption"
        )
        image = _load_image(body, self._images) if body else None
        if image is None:
            # 没有图片字节时至少留下说明文字，不丢内容
            if caption:
                self._add_caption(caption, page_no, bbox)
            return
        with image:
            doctools.add_picture(
                self._document,
                image,
                caption=caption or None,
                page_no=page_no,
                bbox=bbox,
            )

    def _add_equation(self, block: Mapping[str, Any], page_no: int, bbox: Any) -> None:
        text = _inline_text(block.get("content"))
        if text:
            doctools.add_paragraph(
                self._document,
                text,
                label=DocItemLabel.FORMULA,
                page_no=page_no,
                bbox=bbox,
            )

    def _add_code(self, block: Mapping[str, Any], page_no: int, bbox: Any) -> None:
        body = _child_of_type(block, "code_body") or _child_of_type(block, "algorithm_body")
        text = _inline_text((body or {}).get("content"))
        if text:
            doctools.add_paragraph(
                self._document,
                text,
                label=DocItemLabel.CODE,
                page_no=page_no,
                bbox=bbox,
            )

    def _add_caption(self, text: str, page_no: int, bbox: Any) -> None:
        doctools.add_paragraph(
            self._document,
            text,
            label=DocItemLabel.CAPTION,
            page_no=page_no,
            bbox=bbox,
        )

    # ---- 坐标 ----
    def _bbox(
        self, block: Mapping[str, Any], page_idx: int
    ) -> tuple[float, float, float, float] | None:
        bbox = block.get("bbox")
        size = self._page_sizes.get(page_idx)
        if not bbox or size is None:
            return None
        try:
            x0, y0, x1, y1 = (float(value) for value in bbox)
        except (TypeError, ValueError):
            return None
        width, height = size
        # 归一化坐标的原点在左上，转成 PDF 坐标（原点左下，单位点）：
        # (left, top, right, bottom)
        return (x0 * width, (1 - y0) * height, x1 * width, (1 - y1) * height)


def _mark_furniture(item: Any) -> None:
    from docling_core.types.doc.common.content_layer import ContentLayer

    item.content_layer = ContentLayer.FURNITURE


def _page_sizes(middle: Mapping[str, Any]) -> dict[int, tuple[float, float]]:
    extension = (middle.get("extensions") or {}).get(_LAYOUT_EXTENSION) or {}
    sizes: dict[int, tuple[float, float]] = {}
    for page in extension.get("pages") or []:
        if not isinstance(page, Mapping):
            continue
        try:
            page_idx = int(page["page_idx"])
            width = float(page["width_pt"])
            height = float(page["height_pt"])
        except (KeyError, TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            sizes[page_idx] = (width, height)
    return sizes


def _heading_levels(middle: Mapping[str, Any]) -> list[int]:
    levels: list[int] = []
    for page in middle.get("pages") or []:
        for block in (page or {}).get("blocks") or []:
            if not isinstance(block, Mapping):
                continue
            default = _HEADING_TYPES.get(str(block.get("type")))
            if default is None:
                continue
            try:
                levels.append(int(block.get("level") or default))
            except (TypeError, ValueError):
                levels.append(default)
    return levels


def _inline_text(content: Any) -> str:
    """把行内 span 列表拼成文字；公式、行内代码按原文保存。"""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for span in content:
        if not isinstance(span, Mapping):
            continue
        span_type = str(span.get("type") or "")
        if span_type == "hyperlink":
            parts.append(_inline_text(span.get("content")))
        elif span_type in _INLINE_TYPES:
            parts.append(str(span.get("content") or ""))
    return "".join(parts).strip()


def _child_of_type(block: Mapping[str, Any], child_type: str) -> Mapping[str, Any] | None:
    for child in block.get("content") or []:
        if isinstance(child, Mapping) and str(child.get("type")) == child_type:
            return child
    return None


def _annotation_text(block: Mapping[str, Any], annotation_type: str) -> str:
    parts = []
    for child in block.get("content") or []:
        if isinstance(child, Mapping) and str(child.get("type")) == annotation_type:
            text = _inline_text(child.get("content"))
            if text:
                parts.append(text)
    return "\n".join(parts)


def _split_marker(text: str) -> tuple[str | None, bool, str]:
    """把列表项文字开头的序号或项目符号拆出来，避免导出时出现两个标记。"""
    for pattern in _MARKERS:
        match = pattern.match(text)
        if match is None:
            continue
        marker = match.group(0).strip()
        body = text[match.end() :].strip() or marker
        enumerated = match.re.groups > 0 and match.group(1) is not None
        return marker, enumerated, body
    return None, False, text


def _load_image(body: Mapping[str, Any], images: Mapping[str, bytes]) -> Any:
    """从 ZIP 的 images/ 里取图片；没有时退回块里的 image_base64。"""
    from PIL import Image

    path = body.get("image_path") or body.get("image_url")
    data: bytes | None = None
    if isinstance(path, str) and path:
        data = images.get(path) or images.get(path.split("/")[-1])
    if data is None:
        inline = body.get("image_base64")
        if isinstance(inline, str) and inline:
            try:
                data = base64.b64decode(inline.split(",", 1)[-1])
            except (ValueError, TypeError):
                data = None
    if data is None:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except OSError:
        return None
    return image


# ---- 表格 HTML ----
@dataclass
class _Cell:
    text: str
    row_span: int = 1
    col_span: int = 1


class _TableHTMLParser(HTMLParser):
    """只解析表格结构：tr / td / th 的 rowspan、colspan 和单元格文字。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[_Cell]] = []
        self._row: list[_Cell] | None = None
        self._cell: _Cell | None = None
        self._text: list[str] = []
        self._in_thead = False
        self.thead_rows = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "thead":
            self._in_thead = True
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            if self._row is None:
                self._row = []
            values = {key.lower(): (value or "") for key, value in attrs}
            self._cell = _Cell(
                text="",
                row_span=_positive_int(values.get("rowspan"), 1),
                col_span=_positive_int(values.get("colspan"), 1),
            )
            self._text = []
        elif tag in ("br", "p", "li", "div") and self._cell is not None:
            self._text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "thead":
            self._in_thead = False
        elif tag in ("td", "th") and self._cell is not None:
            self._cell.text = _clean_cell_text("".join(self._text))
            if self._row is not None:
                self._row.append(self._cell)
            self._cell = None
            self._text = []
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
                if self._in_thead:
                    self.thead_rows += 1
            self._row = None
        elif tag == "table":
            self._flush_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._text.append(data)

    def close(self) -> None:
        super().close()
        self._flush_row()

    def _flush_row(self) -> None:
        if self._row:
            self.rows.append(self._row)
        self._row = None
        self._cell = None


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _clean_cell_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def parse_table_html(html: str) -> tuple[list[list[str]], list[tuple[int, int, int, int]], int]:
    """解析表格 HTML，返回（二维文字、合并单元格、表头行数）。

    合并单元格的元素是 ``(起始行, 起始列, 行数, 列数)``，行列从 0 开始；
    被合并覆盖的位置在二维文字里是空串。表头行数取 ``<thead>`` 的行数，
    没有 ``<thead>`` 时按 1 行表头处理（与内置表格解析的默认值一致）。
    """
    if not html.strip():
        return [], [], 1
    parser = _TableHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - 结构异常的 HTML 不阻断整个解析
        return [], [], 1

    rows = parser.rows
    if not rows:
        return [], [], 1

    occupied: set[tuple[int, int]] = set()
    grid: dict[tuple[int, int], str] = {}
    merges: list[tuple[int, int, int, int]] = []
    max_col = 0
    for row_index, row in enumerate(rows):
        col = 0
        for cell in row:
            while (row_index, col) in occupied:
                col += 1
            for r in range(row_index, row_index + cell.row_span):
                for c in range(col, col + cell.col_span):
                    occupied.add((r, c))
            if cell.row_span > 1 or cell.col_span > 1:
                merges.append((row_index, col, cell.row_span, cell.col_span))
            grid[(row_index, col)] = cell.text
            col += cell.col_span
            max_col = max(max_col, col)

    if max_col == 0:
        return [], [], 1
    table = [[grid.get((r, c), "") for c in range(max_col)] for r in range(len(rows))]
    header_rows = parser.thead_rows or 1
    return table, merges, min(header_rows, len(rows))
