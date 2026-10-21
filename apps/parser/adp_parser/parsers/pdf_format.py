from __future__ import annotations

import io
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

import pdfplumber
import pypdf
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect

from ..builder import ElementBuilder, clean_cell, table_row_text
from ..config import Limits
from ..errors import ParserError, ScannedPdfError
from ..extensions import get_ocr_provider


@dataclass
class PdfLine:
    text: str
    top: float
    bottom: float
    x0: float
    x1: float
    size: float
    bold_ratio: float


@dataclass
class PdfTable:
    top: float
    rows: list[list[str]]


@dataclass
class PdfPage:
    number: int
    width: float
    lines: list[PdfLine] = field(default_factory=list)
    tables: list[PdfTable] = field(default_factory=list)
    ocr_text: str | None = None
    text_error: bool = False


def parse_pdf(
    data: bytes,
    builder: ElementBuilder,
    limits: Limits,
    *,
    file_name: str = "",
    mime: str | None = None,
) -> str:
    _reject_encrypted(data)

    try:
        pages = _collect_with_pdfplumber(data, builder, limits)
        if pages and all(page.text_error for page in pages) and not any(
            page.lines or page.tables for page in pages
        ):
            raise RuntimeError("all PDF pages failed text extraction")
        engine = "pdfplumber"
    except ParserError:
        raise
    except (PDFPasswordIncorrect, PDFEncryptionError) as exc:
        raise ParserError(
            "pdf_encrypted",
            "PDF 已加密或需要密码，无法解析。请上传未加密的 PDF。",
            422,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - fallback parser is intentional
        builder.warn(
            "pdfplumber 解析失败，已回退到 pypdf；回退结果不包含表格和标题结构"
            f"（原因：{type(exc).__name__}）。"
        )
        used_ocr = _parse_with_pypdf(
            data, builder, limits, file_name=file_name, mime=mime
        )
        return f"pypdf+{_ocr_name()}" if used_ocr else "pypdf"

    _apply_ocr_to_empty_pages(
        pages, data, builder, file_name=file_name, mime=mime
    )
    emitted = _emit_pdfplumber_pages(pages, builder, limits)
    if _count_ocr_pages(pages):
        engine = f"pdfplumber+{_ocr_name()}"
    if not emitted:
        raise ScannedPdfError(
            "PDF 未包含可提取的文本层，可能是扫描件或纯图片 PDF。"
            "请上传带文本层的文档；扫描件需 OCR，当前版本未内置。"
        )
    return engine


def _reject_encrypted(data: bytes) -> None:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception:  # noqa: BLE001 - let the real parser report malformed PDFs
        return
    if not reader.is_encrypted:
        return
    try:
        result = reader.decrypt("")
    except Exception as exc:
        raise ParserError(
            "pdf_encrypted",
            "PDF 已加密或需要密码，无法解析。请上传未加密的 PDF。",
            422,
        ) from exc
    if not result:
        raise ParserError(
            "pdf_encrypted",
            "PDF 已加密或需要密码，无法解析。请上传未加密的 PDF。",
            422,
        )


def _collect_with_pdfplumber(
    data: bytes, builder: ElementBuilder, limits: Limits
) -> list[PdfPage]:
    pages: list[PdfPage] = []
    with pdfplumber.open(io.BytesIO(data), password="") as pdf:
        total = len(pdf.pages)
        if total == 0:
            raise ParserError("empty_pdf", "PDF 不包含任何页面。", 422)
        if total > limits.max_pdf_pages:
            builder.warn(
                f"PDF 共 {total} 页，超过单次解析上限 {limits.max_pdf_pages} 页，"
                "仅解析前 "
                f"{limits.max_pdf_pages} 页。"
            )
        for page_no, page in enumerate(pdf.pages[: limits.max_pdf_pages], start=1):
            page_data = PdfPage(number=page_no, width=float(page.width))
            try:
                raw_lines = page.extract_text_lines() or []
            except Exception as exc:  # noqa: BLE001
                builder.warn(f"PDF 第 {page_no} 页文本提取失败（{type(exc).__name__}）。")
                page_data.text_error = True
                raw_lines = []

            table_specs: list[tuple[tuple[float, float, float, float], list[list[str]]]] = []
            try:
                found_tables = page.find_tables()
            except Exception as exc:  # noqa: BLE001
                builder.warn(f"PDF 第 {page_no} 页表格检测失败（{type(exc).__name__}）。")
                found_tables = []

            sparse_tables = 0
            for table in found_tables:
                try:
                    extracted = table.extract()
                except Exception:  # noqa: BLE001
                    continue
                cleaned: list[list[str]] = []
                for row in extracted or []:
                    cells = [clean_cell(cell) for cell in (row or [])]
                    if any(cells):
                        cleaned.append(cells)
                if len(cleaned) < 2 or max((len(row) for row in cleaned), default=0) < 2:
                    continue
                total_cells = sum(len(row) for row in cleaned)
                non_empty = sum(1 for row in cleaned for cell in row if cell)
                if total_cells == 0 or non_empty / total_cells < 0.35:
                    sparse_tables += 1
                    continue
                bbox = tuple(float(value) for value in table.bbox)  # type: ignore[arg-type]
                table_specs.append((bbox, cleaned))
            if sparse_tables:
                builder.warn(
                    f"PDF 第 {page_no} 页检测到疑似表格但单元格过空，"
                    "已按普通文本行输出，避免错误切分。"
                )

            table_boxes = [bbox for bbox, _ in table_specs]
            for bbox, rows in table_specs:
                page_data.tables.append(PdfTable(top=bbox[1], rows=rows))

            for raw_line in raw_lines:
                text = _clean_pdf_text(raw_line.get("text", ""))
                if not text:
                    continue
                chars = raw_line.get("chars") or []
                if chars and _line_mostly_in_boxes(chars, table_boxes):
                    continue
                sizes = [
                    float(char.get("size", 0))
                    for char in chars
                    if float(char.get("size", 0) or 0) > 0
                ]
                size = statistics.median(sizes) if sizes else 0.0
                bold = 0
                for char in chars:
                    font = str(char.get("fontname", "")).lower()
                    if any(token in font for token in ("bold", "black", "heavy", "demi", "semibold")):
                        bold += 1
                bold_ratio = bold / len(chars) if chars else 0.0
                page_data.lines.append(
                    PdfLine(
                        text=text,
                        top=float(raw_line.get("top", 0.0)),
                        bottom=float(raw_line.get("bottom", 0.0)),
                        x0=float(raw_line.get("x0", 0.0)),
                        x1=float(raw_line.get("x1", 0.0)),
                        size=size,
                        bold_ratio=bold_ratio,
                    )
                )
            pages.append(page_data)
    return pages


def _line_mostly_in_boxes(chars: list[dict], boxes: list[tuple[float, float, float, float]]) -> bool:
    if not boxes:
        return False
    inside = 0
    for char in chars:
        cx = (float(char.get("x0", 0)) + float(char.get("x1", 0))) / 2
        cy = (float(char.get("top", 0)) + float(char.get("bottom", 0))) / 2
        if any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes):
            inside += 1
    return inside / max(1, len(chars)) > 0.5


def _apply_ocr_to_empty_pages(
    pages: list[PdfPage],
    data: bytes,
    builder: ElementBuilder,
    *,
    file_name: str,
    mime: str | None,
) -> None:
    empty_pages = [page for page in pages if not page.lines and not page.tables]
    if not empty_pages:
        return
    provider = get_ocr_provider()
    if provider is None:
        for page in empty_pages:
            builder.warn(
                f"PDF 第 {page.number} 页未提取到文本，可能是扫描页或图片页；"
                "未注册 OCR/视觉 provider，未执行 OCR。"
            )
        return
    try:
        ocr_pages = provider.extract(data, file_name, mime)
    except Exception as exc:  # noqa: BLE001
        builder.warn(
            f"OCR/视觉 provider {provider.name} 调用失败（{type(exc).__name__}），"
            "相关页面仍未提取文本。"
        )
        return
    by_page = {item.page: item.text for item in ocr_pages if item.text and item.text.strip()}
    for page in empty_pages:
        text = by_page.get(page.number)
        if text:
            page.ocr_text = text
            builder.warn(
                f"PDF 第 {page.number} 页无文本层，已使用 OCR/视觉 provider "
                f"{provider.name} 的结果。"
            )
        else:
            builder.warn(
                f"PDF 第 {page.number} 页无文本层，OCR/视觉 provider "
                f"{provider.name} 未返回文本。"
            )


def _count_ocr_pages(pages: list[PdfPage]) -> int:
    return sum(1 for page in pages if page.ocr_text)


def _ocr_name() -> str:
    provider = get_ocr_provider()
    return provider.name if provider else "ocr"


def _emit_pdfplumber_pages(
    pages: list[PdfPage], builder: ElementBuilder, limits: Limits
) -> bool:
    all_lines = [line for page in pages for line in page.lines]
    size_weights: Counter[float] = Counter()
    for line in all_lines:
        if line.size > 0:
            size_weights[round(line.size, 1)] += max(1, len(line.text))
    body_size = _weighted_median(size_weights)
    if body_size <= 0:
        body_size = 10.0

    threshold = max(body_size * 1.12, body_size + 0.9)
    heading_sizes = sorted(
        {
            round(line.size, 1)
            for line in all_lines
            if line.size >= threshold and len(line.text) <= 240
        },
        reverse=True,
    )
    clusters = _cluster_sizes(heading_sizes)
    bold_level = min(6, len(clusters) + 1) if clusters else 1
    heading_found = False
    table_group = 0

    for page in pages:
        blocks: list[dict] = []
        tables_by_top = sorted(page.tables, key=lambda item: item.top)
        for table in tables_by_top:
            blocks.append({"kind": "table", "top": table.top, "table": table})

        lines = sorted(page.lines, key=lambda item: (item.top, item.x0))
        index = 0
        while index < len(lines):
            line = lines[index]
            level = _heading_level(line, body_size, threshold, clusters, bold_level)
            if level is not None:
                heading_found = True
                blocks.append(
                    {
                        "kind": "heading",
                        "top": line.top,
                        "text": line.text,
                        "level": level,
                    }
                )
                index += 1
                continue

            paragraph_lines = [line.text]
            bottom = line.bottom
            index += 1
            while index < len(lines):
                candidate = lines[index]
                if _heading_level(candidate, body_size, threshold, clusters, bold_level):
                    break
                gap = candidate.top - bottom
                gap_limit = max(2.5, min(8.0, candidate.size * 0.6 if candidate.size else 5.0))
                if gap > gap_limit:
                    break
                if abs(candidate.size - line.size) > 1.2:
                    break
                paragraph_lines.append(candidate.text)
                bottom = candidate.bottom
                index += 1
            blocks.append(
                {
                    "kind": "paragraph",
                    "top": line.top,
                    "text": " ".join(paragraph_lines),
                }
            )

        if page.ocr_text:
            for paragraph in re.split(r"\n\s*\n", page.ocr_text):
                text = _clean_pdf_text(paragraph)
                if text:
                    blocks.append({"kind": "paragraph", "top": 0.0, "text": text})

        blocks.sort(key=lambda item: item.get("top", 0.0))
        for block in blocks:
            if builder.at_element_limit:
                return True
            if block["kind"] == "heading":
                builder.add_heading(block["text"], block["level"], page=page.number)
            elif block["kind"] == "paragraph":
                builder.add("paragraph", block["text"], page=page.number)
            else:
                table_group += 1
                table: PdfTable = block["table"]
                header_index = next(
                    (i for i, row in enumerate(table.rows) if any(row)), 0
                )
                for row_index, row in enumerate(table.rows, start=1):
                    element_type = (
                        "table_header" if row_index - 1 == header_index else "table_row"
                    )
                    builder.add(
                        element_type,
                        table_row_text(row),
                        page=page.number,
                        row=row_index,
                        table_group=table_group,
                    )
                    if builder.at_element_limit:
                        return True

    if heading_found:
        builder.warn(
            "PDF 无原生标题结构；heading/level 由字号和粗体启发式推断，可能不准确。"
        )
    else:
        builder.warn("PDF 未识别到标题层级，已按页输出段落和表格。")

    text_pages = sum(1 for page in pages if page.lines or page.tables or page.ocr_text)
    return text_pages > 0


def _heading_level(
    line: PdfLine,
    body_size: float,
    threshold: float,
    clusters: list[float],
    bold_level: int,
) -> int | None:
    text = line.text.strip()
    if not text or len(text) > 240 or re.fullmatch(r"[\d\s\-–—.()]+", text):
        return None
    if line.size >= threshold:
        level = _level_for_size(line.size, clusters)
        if level is not None:
            return level
    if (
        line.size >= body_size * 0.98
        and line.bold_ratio >= 0.85
        and len(text) <= 80
        and not re.search(r"[.,;:!?。，；：！？]$", text)
        and not text.startswith(("•", "-", "–", "—", "*"))
        and not re.match(r"^(page|第\s*\d+)\s*[\/\\]?\s*\d*$", text, re.IGNORECASE)
    ):
        return bold_level
    return None


def _cluster_sizes(sizes: list[float], tolerance: float = 0.75) -> list[float]:
    clusters: list[list[float]] = []
    for size in sorted({round(value, 1) for value in sizes}, reverse=True):
        if not clusters or abs(size - clusters[-1][-1]) > tolerance:
            clusters.append([size])
        else:
            clusters[-1].append(size)
    return [round(sum(group) / len(group), 1) for group in clusters]


def _level_for_size(size: float, clusters: list[float]) -> int | None:
    if not clusters:
        return None
    nearest = min(range(len(clusters)), key=lambda i: abs(size - clusters[i]))
    return min(6, nearest + 1)


def _weighted_median(counter: Counter[float]) -> float:
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    halfway = total / 2
    cumulative = 0
    for value in sorted(counter):
        cumulative += counter[value]
        if cumulative >= halfway:
            return float(value)
    return float(max(counter))


def _clean_pdf_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()


def _parse_with_pypdf(
    data: bytes,
    builder: ElementBuilder,
    limits: Limits,
    *,
    file_name: str,
    mime: str | None,
) -> bool:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ParserError(
            "unreadable_pdf",
            f"PDF 解析失败：{type(exc).__name__}。文件可能损坏或格式不受支持。",
            422,
        ) from exc

    empty_pages: list[int] = []
    page_texts: list[tuple[int, str]] = []
    total = len(reader.pages)
    if total == 0:
        raise ParserError("empty_pdf", "PDF 不包含任何页面。", 422)
    if total > limits.max_pdf_pages:
        builder.warn(
            f"PDF 共 {total} 页，超过单次解析上限 {limits.max_pdf_pages} 页，"
            f"仅解析前 {limits.max_pdf_pages} 页。"
        )
    for page_no, page in enumerate(reader.pages[: limits.max_pdf_pages], start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            builder.warn(f"PDF 第 {page_no} 页文本提取失败（{type(exc).__name__}）。")
            text = ""
        text = text.replace("\xa0", " ")
        if text.strip():
            page_texts.append((page_no, text))
        else:
            empty_pages.append(page_no)

    ocr_texts: dict[int, str] = {}
    if empty_pages:
        provider = get_ocr_provider()
        if provider is None:
            for page_no in empty_pages:
                builder.warn(
                    f"PDF 第 {page_no} 页未提取到文本，可能是扫描页或图片页；"
                    "未注册 OCR/视觉 provider，未执行 OCR。"
                )
        else:
            try:
                results = provider.extract(data, file_name, mime)
                ocr_texts = {
                    item.page: item.text
                    for item in results
                    if item.text and item.text.strip()
                }
            except Exception as exc:  # noqa: BLE001
                builder.warn(
                    f"OCR/视觉 provider {provider.name} 调用失败"
                    f"（{type(exc).__name__}）。"
                )

    emitted = False
    for page_no, text in page_texts:
        for paragraph in re.split(r"\n\s*\n", text):
            cleaned = _clean_pdf_text(paragraph)
            if cleaned:
                builder.add("paragraph", cleaned, page=page_no)
                emitted = True
    for page_no in empty_pages:
        text = ocr_texts.get(page_no)
        if not text:
            continue
        for paragraph in re.split(r"\n\s*\n", text):
            cleaned = _clean_pdf_text(paragraph)
            if cleaned:
                builder.add("paragraph", cleaned, page=page_no)
                emitted = True
        builder.warn(
            f"PDF 第 {page_no} 页无文本层，已使用 OCR/视觉 provider 的结果。"
        )

    if not emitted:
        raise ScannedPdfError(
            "PDF 未包含可提取的文本层，可能是扫描件或纯图片 PDF。"
            "请上传带文本层的文档；扫描件需 OCR，当前版本未内置。"
        )
    return bool(ocr_texts)
