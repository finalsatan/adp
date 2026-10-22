"""构造 DoclingDocument 的辅助函数，供各解析引擎使用（X03）。

所有元素统一在这里设置 ``prov``：页码从 1 开始，坐标用 PDF 坐标系（原点在
左下，单位点）。没有页码的文档（TXT、Word）传 ``page_no=None``，此时不设
``prov``（需求 4.1.8：Word 没有页码）。

表格的坐标和跨行跨列按 DoclingDocument 的 ``TableData`` 表达；每个数据行到
原始表格行的对应关系由引擎自己决定（Excel 用页面号表示工作表，见 X04）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ImageRef, ProvenanceItem
from docling_core.types.doc.document import (
    DocItemLabel,
    DoclingDocument,
    GroupItem,
    TableCell,
    TableData,
)
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.text import TextItem

__all__ = [
    "add_heading",
    "add_page",
    "add_paragraph",
    "add_picture",
    "add_table",
    "build_table_data",
    "new_document",
    "provenance",
]

# 图片坐标按 PDF 坐标系存储，dpi 只用于记录图片分辨率（图片本身以像素保存）
IMAGE_DPI = 72

BBox = tuple[float, float, float, float]
"""PDF 坐标下的包围框 (left, top, right, bottom)。"""

Merge = tuple[int, int, int, int]
"""合并单元格 (起始行, 起始列, 行数, 列数)，行列从 0 开始。"""


def new_document(name: str) -> DoclingDocument:
    """新建一个空的 DoclingDocument。"""
    return DoclingDocument(name=name)


def add_page(document: DoclingDocument, page_no: int, width: float, height: float) -> None:
    """加一页。``page_no`` 从 1 开始，``width``/``height`` 为点。"""
    document.add_page(page_no, Size(width=width, height=height))


def provenance(page_no: int, bbox: BBox | None = None, charspan: tuple[int, int] = (0, 0)):
    """构造一个 ``ProvenanceItem``（页码从 1 开始，坐标原点在左下）。"""
    box = BoundingBox(
        l=bbox[0] if bbox else 0.0,
        t=bbox[1] if bbox else 0.0,
        r=bbox[2] if bbox else 0.0,
        b=bbox[3] if bbox else 0.0,
        coord_origin=CoordOrigin.BOTTOMLEFT,
    )
    return ProvenanceItem(page_no=page_no, bbox=box, charspan=charspan)


def _text_prov(page_no: int | None, bbox: BBox | None, text: str):
    if page_no is None:
        return None
    return provenance(page_no, bbox, charspan=(0, len(text)))


def add_heading(
    document: DoclingDocument,
    text: str,
    level: int = 1,
    *,
    page_no: int | None = None,
    bbox: BBox | None = None,
    parent: GroupItem | None = None,
) -> TextItem:
    """加一个标题（``level`` 为标题级别，从 1 开始）。"""
    return document.add_heading(
        text=text,
        level=level,
        prov=_text_prov(page_no, bbox, text),
        parent=parent,
    )


def add_paragraph(
    document: DoclingDocument,
    text: str,
    *,
    label: DocItemLabel = DocItemLabel.TEXT,
    page_no: int | None = None,
    bbox: BBox | None = None,
    parent: GroupItem | None = None,
) -> TextItem:
    """加一个段落（默认 ``text`` 标签，可传 ``list_item`` 等）。"""
    return document.add_text(
        label=label,
        text=text,
        prov=_text_prov(page_no, bbox, text),
        parent=parent,
    )


def build_table_data(
    rows: Sequence[Sequence[Any]],
    *,
    header_rows: int = 1,
    merges: Iterable[Merge] = (),
) -> TableData:
    """把二维文本构造为 ``TableData``，支持表头和合并单元格。

    :param rows: 每行一个序列，长度可以不一致，缺的补空。
    :param header_rows: 前几行作为表头（``column_header=True``）。
    :param merges: 合并单元格，元素为 (起始行, 起始列, 行数, 列数)，从 0 开始。
    """
    num_rows = len(rows)
    num_cols = max((len(row) for row in rows), default=0)
    span_at: dict[tuple[int, int], tuple[int, int]] = {}
    covered: set[tuple[int, int]] = set()
    for start_row, start_col, row_span, col_span in merges:
        span_at[(start_row, start_col)] = (row_span, col_span)
        for row in range(start_row, start_row + row_span):
            for col in range(start_col, start_col + col_span):
                if (row, col) != (start_row, start_col):
                    covered.add((row, col))

    cells: list[TableCell] = []
    for row in range(num_rows):
        for col in range(num_cols):
            if (row, col) in covered:
                continue
            row_span, col_span = span_at.get((row, col), (1, 1))
            text = str(rows[row][col]) if col < len(rows[row]) else ""
            cells.append(
                TableCell(
                    text=text,
                    start_row_offset_idx=row,
                    end_row_offset_idx=row + row_span,
                    start_col_offset_idx=col,
                    end_col_offset_idx=col + col_span,
                    row_span=row_span,
                    col_span=col_span,
                    column_header=row < header_rows,
                )
            )
    return TableData(table_cells=cells, num_rows=num_rows, num_cols=num_cols)


def add_table(
    document: DoclingDocument,
    rows: Sequence[Sequence[Any]],
    *,
    header_rows: int = 1,
    merges: Iterable[Merge] = (),
    page_no: int | None = None,
    bbox: BBox | None = None,
    parent: GroupItem | None = None,
) -> TableItem:
    """加一个表格（含表头和合并单元格）。"""
    data = build_table_data(rows, header_rows=header_rows, merges=merges)
    return document.add_table(
        data=data,
        prov=provenance(page_no, bbox) if page_no is not None else None,
        parent=parent,
    )


def add_picture(
    document: DoclingDocument,
    image: Any,
    *,
    caption: str | None = None,
    page_no: int | None = None,
    bbox: BBox | None = None,
    parent: GroupItem | None = None,
) -> PictureItem:
    """加一张图片。

    :param image: PIL Image，或已经构造好的 ``ImageRef``。
    :param caption: 引擎识别出的图中文字，作为图片说明保存。
    """
    image_ref = image if isinstance(image, ImageRef) else _to_image_ref(image)
    caption_item = None
    if caption:
        caption_item = document.add_text(
            label=DocItemLabel.CAPTION,
            text=caption,
            prov=_text_prov(page_no, bbox, caption),
        )
    return document.add_picture(
        image=image_ref,
        caption=caption_item,
        prov=provenance(page_no, bbox) if page_no is not None else None,
        parent=parent,
    )


def _to_image_ref(image: Any) -> ImageRef:
    from PIL import Image as PILImage

    if not isinstance(image, PILImage.Image):
        raise TypeError("image 必须是 PIL.Image.Image 或 ImageRef")
    return ImageRef.from_pil(image, dpi=IMAGE_DPI)


def image_path(name: str) -> Path:
    """图片在 ``doc.json`` 中的相对引用路径（知识库技术方案 11.1）。"""
    return Path("images") / name
