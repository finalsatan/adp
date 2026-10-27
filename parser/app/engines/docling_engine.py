"""Docling 标准 PDF、图片与原生 Office/HTML/Markdown 解析引擎。"""

from __future__ import annotations

import logging
import os
import tempfile
from importlib.metadata import version
from pathlib import Path

import pypdfium2 as pdfium
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    OcrMode,
    PdfPipelineOptions,
    RapidOcrOptions,
    TableFormerMode,
    TableStructureOptions,
)
from docling.datamodel.progress import ConversionProgressKind
from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from PIL import Image

from ..errors import ParseError
from .base import ParseContext, ParseOutput
from .markdown_doc import markdown_file_to_document, normalize_heading_levels
from .mhtml import extract_mhtml

logger = logging.getLogger(__name__)

ENGINE_VERSION = f"docling-{version('docling')}"
_NATIVE = {"word": {"docx"}, "ppt": {"pptx"}, "html": {"html", "htm", "shtml"}, "markdown": {"md"}}
_UNSUPPORTED = {"doc", "rtf", "odt", "ppt", "odp"}
_HTML_EXTS = {"html", "htm", "shtml", "mhtml", "mht"}
_IMAGE_DIRECT = {"png", "jpg", "jpeg", "bmp", "tiff"}
_IMAGE_CONVERT = {"webp", "gif", "jp2"}
# RapidOCR 的 PP-OCR 语言码；镜像里预置的就是这一份（Dockerfile 的 --rapidocr-backend-lang）。
_OCR_LANG = ["ch"]
_SCAN_MODES = ("auto", "force")


class DoclingEngine:
    name = "docling"

    def __init__(self) -> None:
        # 每种扫描页判断一个转换器：Docling 按管线参数缓存管线，重建会重新加载模型。
        self._converters: dict[str, DocumentConverter] = {}

    def supports(self, file_type: str, ext: str) -> bool:
        return (
            file_type == "pdf"
            or ext in _IMAGE_DIRECT | _IMAGE_CONVERT
            or ext
            in {
                "docx",
                "pptx",
                "html",
                "htm",
                "shtml",
                "mhtml",
                "mht",
                "md",
                "doc",
                "rtf",
                "odt",
                "ppt",
                "odp",
            }
        )

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def _get_converter(self, scan_mode: str) -> DocumentConverter:
        converter = self._converters.get(scan_mode)
        if converter is None:
            options = PdfPipelineOptions(
                do_ocr=True,
                do_table_structure=True,
                table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE),
                # 固定用 RapidOCR（CPU，onnxruntime 后端），不交给 Docling 自动挑选引擎
                # （自动挑选在没装 Tesseract/EasyOCR 的镜像里会挑到别的引擎或直接不可用）。
                ocr_options=RapidOcrOptions(backend="onnxruntime", lang=list(_OCR_LANG)),
            )
            if scan_mode == "force":
                options.ocr_options.mode = OcrMode.FULL_PAGE
            artifacts = Path(os.environ.get("DOCLING_ARTIFACTS_PATH", "/opt/docling-models"))
            if artifacts.is_dir():
                options.artifacts_path = artifacts
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_cls=StandardPdfPipeline, pipeline_options=options
                    ),
                    InputFormat.IMAGE: ImageFormatOption(
                        pipeline_cls=StandardPdfPipeline, pipeline_options=options
                    ),
                }
            )
            self._converters[scan_mode] = converter
        return converter

    def warmup(self) -> None:
        """把模型读进内存（X01 的 pool 在每个子进程报到 ready 前调用）。"""
        self._get_converter("auto").initialize_pipeline(InputFormat.PDF)

    def parse(self, ctx: ParseContext) -> ParseOutput:
        if not self.supports(ctx.file_type, ctx.ext):
            raise ParseError("UNSUPPORTED_TYPE", f"Docling 引擎不支持 {ctx.file_type} 类型")
        if ctx.ext in _UNSUPPORTED:
            raise ParseError("UNSUPPORTED_TYPE", "请先转换为 docx / pptx")
        if ctx.ext not in _IMAGE_DIRECT | _IMAGE_CONVERT and ctx.file_type == "image":
            raise ParseError("UNSUPPORTED_TYPE", f"Docling 不支持 {ctx.ext} 图片")
        return _parse(ctx, self)


def _page_progress(ctx: ParseContext, fallback_pages: int):
    """Docling 的页级进度回调：只关心“每页处理完”事件。"""

    def callback(event) -> None:
        if getattr(event, "kind", None) != ConversionProgressKind.PAGE_COMPLETED:
            return
        total = int(getattr(event, "total_pages", 0) or fallback_pages)
        ctx.report(int(getattr(event, "completed_pages", 0)), total)

    return callback


def parse_native(ctx: ParseContext) -> ParseOutput:
    """供 builtin 引擎转发 Markdown/HTML。"""
    return _parse(ctx, DoclingEngine())


def _parse(ctx: ParseContext, engine: DoclingEngine) -> ParseOutput:
    scan_mode = ctx.engine_params.get("scan_mode", "auto")
    if scan_mode not in _SCAN_MODES:
        raise ParseError("INVALID_PARAMS", "scan_mode 必须为 auto 或 force")
    input_path = Path(ctx.input_path)
    temp_root = Path(ctx.work_dir)
    temp_root.mkdir(parents=True, exist_ok=True)
    temp = None
    ocr_pages = 0
    pages = 0
    no_text_pages: set[int] = set()
    if ctx.file_type == "pdf":
        try:
            with pdfium.PdfDocument(str(input_path)) as pdf:
                pages = len(pdf)
                if scan_mode == "force":
                    ocr_pages = pages
                else:
                    no_text_pages = {
                        index
                        for index, page in enumerate(pdf, start=1)
                        if page.get_textpage().count_chars() == 0
                    }
                    ocr_pages = len(no_text_pages)
        except Exception as exc:  # noqa: BLE001
            raise ParseError("FILE_CORRUPTED", "PDF 文件无法读取") from exc
    elif ctx.file_type == "ppt" and ctx.ext == "pptx":
        from pptx import Presentation

        pages = len(Presentation(str(input_path)).slides)
    elif ctx.file_type == "image":
        pages = 1
        ocr_pages = 1
    try:
        source = input_path
        if ctx.ext in _IMAGE_CONVERT:
            temp = tempfile.TemporaryDirectory(dir=ctx.work_dir)
            converted = Path(temp.name) / "converted.png"
            with Image.open(input_path) as image:
                if ctx.ext == "gif":
                    image.seek(0)
                image.convert("RGB").save(converted, "PNG")
            source = converted
        elif ctx.ext in {"mhtml", "mht"}:
            temp = tempfile.TemporaryDirectory(dir=ctx.work_dir)
            source = extract_mhtml(str(input_path), str(Path(temp.name) / "body.html"))
        elif ctx.ext in {"html", "htm", "shtml"}:
            # 同一安全过滤器也作用于普通 HTML，删除脚本和远程资源引用。
            temp = tempfile.TemporaryDirectory(dir=ctx.work_dir)
            from .mhtml import _RemoteResourceStripper

            class SafeHTML(_RemoteResourceStripper):
                pass

            parser = SafeHTML()
            parser.feed(input_path.read_text(encoding="utf-8", errors="replace"))
            parser.close()
            source = Path(temp.name) / "safe.html"
            source.write_text("".join(parser.parts), encoding="utf-8")
        ctx.report(0, pages)
        if ctx.ext == "md":
            # .md 与内置 markdown、MarkItDown 用同一套标题级别修正（X13）
            document = markdown_file_to_document(source, input_path.stem)
        else:
            result = engine._get_converter(scan_mode).convert(
                str(source), progress_callback=_page_progress(ctx, pages)
            )
            document = result.document
            if ctx.file_type == "pdf" and scan_mode == "auto":
                ocr_pages = len(
                    no_text_pages
                    | {
                        page.page_no
                        for page in result.pages
                        if any(getattr(cell, "from_ocr", False) for cell in page.cells)
                    }
                )
            if ctx.ext in _HTML_EXTS:
                # Docling 的 HTML 后端把 <h1> 当文档标题（title），和 Markdown
                # 后端一样按“1 级标题是 1 级”修正（X13）
                normalize_heading_levels(document)
        if not pages:
            pages = len(document.pages)
        if ctx.file_type == "ppt":
            for index, page in enumerate(document.pages.values(), start=1):
                page.page_no = index
            _add_pptx_tables(input_path, document)
        ctx.report(pages, pages)
        return ParseOutput(
            document, pages=pages, ocr_pages=ocr_pages, engine_version=ENGINE_VERSION
        )
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError("FILE_CORRUPTED", f"Docling 解析失败：{exc}") from exc
    finally:
        if temp is not None:
            temp.cleanup()


def _add_pptx_tables(path: Path, document) -> None:
    """Docling 2.142.1 的 PPTX 后端忽略原生表格时，用 Office 结构保留表格。"""
    from pptx import Presentation

    from .. import doctools

    presentation = Presentation(str(path))
    tables = [
        (page_no, shape)
        for page_no, slide in enumerate(presentation.slides, start=1)
        for shape in slide.shapes
        if shape.has_table
    ]
    if not tables or len(document.tables) >= len(tables):
        return
    for page_no, shape in tables:
        rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
        doctools.add_table(
            document,
            rows,
            page_no=page_no,
            bbox=(
                float(shape.left),
                float(shape.top),
                float(shape.left + shape.width),
                float(shape.top + shape.height),
            ),
        )
