"""pypdfium PDF 引擎：只读文字层，不做 OCR（知识库技术方案 6.2、需求 4.1.4）。

用 pypdfium2 逐页读取字符和坐标，先按 y 中心合并成行、再按行距合并成段落，
每个段落一个文字元素，``prov`` 带页码和包围框（PDF 坐标，原点左下，单位点）。
没有文字层的页解析为空，页码记入 ``empty_pages``。

本文件的行分组、行内空格推断、隐藏文字过滤和重复页眉页脚判断参考 WeKnora
DocReader 的 ``docreader/parser/pdf_parser.py``（MIT 许可，
https://github.com/Tencent/WeKnora ），按其思路用 pypdfium2 重写；只保留本卡
需要的最快路径，不做渲染、OCR、图表和图片抽取。
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from .. import doctools
from ..errors import ParseError
from .base import ParseContext, ParseOutput

__all__ = ["PypdfiumEngine", "parse_pdf"]

ENGINE_VERSION = "pypdfium-0.1"

# 重复页眉页脚：只把每页首、尾的短行当候选，在足够多的页面上出现才算
_MAX_EDGE_LINE_CHARS = 80
_REPEAT_MIN_PAGES = 2
_REPEAT_PAGE_FRACTION = 0.6

# 行内推断词间空格：间距超过字符宽中位数的这个比例就补一个空格
_WORD_GAP_WIDTH_RATIO = 0.4
# 同一行的字符：y 中心相差不超过行高中位数的这个比例
_LINE_Y_TOLERANCE = 0.5
# 段落合并：相邻两行间距不超过行高的这个比例时视为同一段
_PARAGRAPH_GAP_RATIO = 1.0

# 句末标点：上一行以这些结尾时不再与下一行合并
_SENTENCE_END = re.compile(r"[。．.!！?？；;]$")
_DIGITS = re.compile(r"\d+")
# 中日韩文字和标点：两侧都是这些字符时，合并行不插空格
_CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


@dataclass
class _Char:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    index: int


@dataclass
class _Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    height: float

    @property
    def bbox(self) -> doctools.BBox:
        """PDF 坐标下的包围框 (left, top, right, bottom)。"""
        return (self.x0, self.y1, self.x1, self.y0)


class PypdfiumEngine:
    """最快档 PDF 引擎：只读文字层。"""

    name = "pypdfium"

    def supports(self, file_type: str, ext: str) -> bool:
        return file_type == "pdf" and ext == "pdf"

    def availability(self) -> tuple[bool, str | None]:
        # pypdfium2 随依赖安装，无需外部部署
        return True, None

    def parse(self, ctx: ParseContext) -> ParseOutput:
        if not self.supports(ctx.file_type, ctx.ext):
            raise ParseError("UNSUPPORTED_TYPE", f"pypdfium 引擎不支持 {ctx.file_type} 类型")
        return parse_pdf(ctx)


def parse_pdf(ctx: ParseContext) -> ParseOutput:
    """逐页解析 PDF 文字层，返回带页码坐标的 DoclingDocument。"""
    pdf = _open_document(ctx.input_path)
    try:
        total = len(pdf)
        page_lines: dict[int, list[_Line]] = {}
        page_sizes: dict[int, tuple[float, float]] = {}
        empty_pages: list[int] = []
        for index in range(total):
            page = pdf[index]
            try:
                width, height = page.get_size()
                page_sizes[index + 1] = (width, height)
                lines = _page_lines(page)
            finally:
                page.close()
            page_lines[index + 1] = lines
            if not lines:
                empty_pages.append(index + 1)
            ctx.report(index + 1, total)
        document = _build_document(ctx, total, page_lines, page_sizes)
        return ParseOutput(
            document=document,
            pages=total,
            ocr_pages=0,
            engine_version=ENGINE_VERSION,
            empty_pages=empty_pages,
        )
    finally:
        pdf.close()


def _open_document(path: str) -> pdfium.PdfDocument:
    """打开 PDF；加密和损坏分别返回对应错误码。"""
    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise ParseError("FILE_ENCRYPTED") from exc
        raise ParseError("FILE_CORRUPTED") from exc
    except Exception as exc:  # noqa: BLE001 - 兜底为损坏，避免子进程抛未知异常
        raise ParseError("FILE_CORRUPTED") from exc


# ---- 字符与行 ----


def _invisible_boxes(page) -> list[tuple[float, float, float, float]]:
    """渲染模式为不可见的文字对象的包围框（防隐藏文字注入）。"""
    boxes: list[tuple[float, float, float, float]] = []
    try:
        objects = page.get_objects()
    except Exception:  # noqa: BLE001 - 拿不到对象就当没有隐藏文字
        return boxes
    for obj in objects:
        if obj.type != pdfium_raw.FPDF_PAGEOBJ_TEXT:
            continue
        try:
            mode = pdfium_raw.FPDFTextObj_GetTextRenderMode(obj.raw)
        except Exception:  # noqa: BLE001
            continue
        if mode != pdfium_raw.FPDF_TEXTRENDERMODE_INVISIBLE:
            continue
        try:
            left, bottom, right, top = obj.get_bounds()
        except Exception:  # noqa: BLE001
            continue
        boxes.append((min(left, right), min(bottom, top), max(left, right), max(bottom, top)))
    return boxes


def _point_in_boxes(x: float, y: float, boxes: list[tuple[float, float, float, float]]) -> bool:
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes)


def _page_chars(textpage, page) -> tuple[list[_Char], float]:
    """取一页的字符和坐标，过滤不可见、页面外的字符。"""
    count = textpage.count_chars()
    if count <= 0:
        return [], 0.0
    width, height = page.get_size()
    try:
        crop_left, crop_bottom, crop_right, crop_top = page.get_cropbox()
    except Exception:  # noqa: BLE001
        crop_left, crop_bottom, crop_right, crop_top = 0.0, 0.0, width, height
    invisible = _invisible_boxes(page)

    chars: list[_Char] = []
    for i in range(count):
        try:
            # loose=True 用字体的 ascent/descent 作为上下界，同一行的字符高度一致，
            # 避免标点（如英文句点）因紧致框偏下被拆成单独一行
            left, bottom, right, top = textpage.get_charbox(i, loose=True)
        except Exception:  # noqa: BLE001
            continue
        text = textpage.get_text_range(i, 1)
        if text in ("\r", "\n"):
            continue
        x0, x1 = (left, right) if left <= right else (right, left)
        y0, y1 = (bottom, top) if bottom <= top else (top, bottom)
        if x1 < crop_left or x0 > crop_right or y1 < crop_bottom or y0 > crop_top:
            continue  # 页面外
        if invisible and _point_in_boxes((x0 + x1) / 2, (y0 + y1) / 2, invisible):
            continue  # 被不可见文字对象覆盖
        chars.append(_Char(text=text, x0=x0, y0=y0, x1=x1, y1=y1, index=i))
    return chars, width


def _find_split(chars: list[_Char], min_gap: float) -> float | None:
    """在 x 轴上找最宽的干净缝隙（多栏排版的分栏线），没有则返回 None。"""
    intervals = sorted(((c.x0, c.x1) for c in chars), key=lambda iv: iv[0])
    if len(intervals) < 2:
        return None
    cur_end = intervals[0][1]
    best_gap, best_cut = 0.0, None
    for start, end in intervals[1:]:
        gap = start - cur_end
        if gap >= min_gap and gap > best_gap:
            best_gap, best_cut = gap, cur_end + gap / 2
        if end > cur_end:
            cur_end = end
    return best_cut


def _split_columns(
    chars: list[_Char], scale: float, width: float, depth: int = 0
) -> list[list[_Char]]:
    """按分栏线把字符切成阅读顺序的栏。"""
    if len(chars) <= 1 or depth > 10:
        return [chars]
    min_gap = max(scale * 2.5, width * 0.04)
    cut = _find_split(chars, min_gap)
    if cut is None:
        return [chars]
    left = [c for c in chars if (c.x0 + c.x1) / 2 < cut]
    right = [c for c in chars if (c.x0 + c.x1) / 2 >= cut]
    if not left or not right:
        return [chars]
    return _split_columns(left, scale, width, depth + 1) + _split_columns(
        right, scale, width, depth + 1
    )


def _join_line_glyphs(chars: list[_Char]) -> str:
    """按 x 拼一行的字符，用字符间距推断词间空格。"""
    if not chars:
        return ""
    widths = [c.x1 - c.x0 for c in chars if c.x1 > c.x0]
    median_width = statistics.median(widths) if widths else 1.0
    gap_threshold = median_width * _WORD_GAP_WIDTH_RATIO

    parts: list[str] = []
    for i, cur in enumerate(chars):
        if i == 0:
            parts.append(cur.text)
            continue
        prev = chars[i - 1]
        if cur.text.isspace() or prev.text.isspace():
            if not cur.text.isspace() or (parts and not parts[-1].endswith(" ")):
                parts.append(cur.text)
            continue
        if cur.x0 - prev.x1 > gap_threshold:
            parts.append(" ")
        parts.append(cur.text)
    return "".join(parts).strip()


def _group_lines(chars: list[_Char]) -> list[_Line]:
    """把一栏的字符按 y 中心合并成行（从上到下）。"""
    if not chars:
        return []
    heights = [c.y1 - c.y0 for c in chars if c.y1 > c.y0]
    median_height = statistics.median(heights) if heights else 1.0

    ordered = sorted(chars, key=lambda c: -(c.y0 + c.y1) / 2)
    groups: list[list[_Char]] = []
    current: list[_Char] = []
    reference: float | None = None
    for char in ordered:
        center = (char.y0 + char.y1) / 2
        if reference is None or abs(center - reference) <= _LINE_Y_TOLERANCE * median_height:
            current.append(char)
            if reference is None:
                reference = center
        else:
            groups.append(current)
            current = [char]
            reference = center
    if current:
        groups.append(current)

    lines: list[_Line] = []
    for group in groups:
        ordered_group = sorted(group, key=lambda c: c.x0)
        text = _join_line_glyphs(ordered_group)
        if not text:
            continue
        group_heights = [c.y1 - c.y0 for c in ordered_group if c.y1 > c.y0]
        lines.append(
            _Line(
                text=text,
                x0=min(c.x0 for c in ordered_group),
                y0=min(c.y0 for c in ordered_group),
                x1=max(c.x1 for c in ordered_group),
                y1=max(c.y1 for c in ordered_group),
                height=statistics.median(group_heights) if group_heights else median_height,
            )
        )
    return lines


def _page_lines(page) -> list[_Line]:
    """取一页的所有文字行，按栏、再按从上到下的顺序。"""
    textpage = page.get_textpage()
    try:
        chars, width = _page_chars(textpage, page)
        if not chars:
            return []
        heights = [c.y1 - c.y0 for c in chars if c.y1 > c.y0]
        scale = (statistics.median(heights) if heights else 1.0) or 1.0
        lines: list[_Line] = []
        for column in _split_columns(chars, scale, width):
            lines.extend(_group_lines(column))
        return lines
    finally:
        textpage.close()


# ---- 页眉页脚 ----


def _normalize_edge_line(text: str) -> str:
    """把行里的数字替换成 ``#``，让只差数字的页码归为同一行。"""
    return _DIGITS.sub("#", text.strip())


def _repeated_edge_lines(page_lines: dict[int, list[_Line]]) -> set[str]:
    """在多数页面的首、尾重复出现的行（归一化后的文本）。"""
    pages = [lines for lines in page_lines.values() if lines]
    if len(pages) < _REPEAT_MIN_PAGES:
        return set()
    counter: Counter[str] = Counter()
    for lines in pages:
        for edge in {lines[0].text.strip(), lines[-1].text.strip()}:
            if edge and len(edge) <= _MAX_EDGE_LINE_CHARS:
                counter[_normalize_edge_line(edge)] += 1
    threshold = max(2, int(len(pages) * _REPEAT_PAGE_FRACTION))
    return {text for text, count in counter.items() if count >= threshold}


def _line_kind(index: int, total: int, line: _Line, repeated: set[str], page_height: float) -> str:
    """判断一行是正文、页眉还是页脚。"""
    if index not in (0, total - 1):
        return "body"
    if len(line.text) > _MAX_EDGE_LINE_CHARS:
        return "body"
    if _normalize_edge_line(line.text) not in repeated:
        return "body"
    if index == 0 and index == total - 1:
        # 整页只有一行，按上下半页判断
        return "header" if (line.y0 + line.y1) / 2 > page_height / 2 else "footer"
    return "header" if index == 0 else "footer"


# ---- 段落 ----


def _same_paragraph(prev: _Line, cur: _Line) -> bool:
    gap = prev.y0 - cur.y1
    line_height = max(prev.height, cur.height, 1.0)
    if gap > _PARAGRAPH_GAP_RATIO * line_height:
        return False
    return not _SENTENCE_END.search(prev.text)


def _join_paragraph(lines: list[_Line]) -> str:
    text = lines[0].text
    for prev, cur in zip(lines, lines[1:], strict=False):
        if _CJK.search(prev.text[-1:]) and _CJK.search(cur.text[:1]):
            text += cur.text
        else:
            text += " " + cur.text
    return text


def _union_bbox(lines: list[_Line]) -> doctools.BBox:
    return (
        min(line.x0 for line in lines),
        max(line.y1 for line in lines),
        max(line.x1 for line in lines),
        min(line.y0 for line in lines),
    )


def _flush_body(document: DoclingDocument, page_no: int, body: list[_Line]) -> None:
    if not body:
        return
    doctools.add_paragraph(
        document,
        _join_paragraph(body),
        page_no=page_no,
        bbox=_union_bbox(body),
    )
    body.clear()


def _build_document(
    ctx: ParseContext,
    total: int,
    page_lines: dict[int, list[_Line]],
    page_sizes: dict[int, tuple[float, float]],
) -> DoclingDocument:
    document = doctools.new_document(Path(ctx.input_path).stem)
    for page_no in range(1, total + 1):
        width, height = page_sizes.get(page_no, (0.0, 0.0))
        doctools.add_page(document, page_no, width, height)

    repeated = _repeated_edge_lines(page_lines)
    for page_no in range(1, total + 1):
        lines = page_lines.get(page_no, [])
        if not lines:
            continue
        page_height = page_sizes.get(page_no, (0.0, 0.0))[1]
        body: list[_Line] = []
        for index, line in enumerate(lines):
            kind = _line_kind(index, len(lines), line, repeated, page_height)
            if kind == "body":
                if body and not _same_paragraph(body[-1], line):
                    _flush_body(document, page_no, body)
                body.append(line)
                continue
            _flush_body(document, page_no, body)
            label = DocItemLabel.PAGE_HEADER if kind == "header" else DocItemLabel.PAGE_FOOTER
            doctools.add_paragraph(
                document, line.text, label=label, page_no=page_no, bbox=line.bbox
            )
        _flush_body(document, page_no, body)
    return document
