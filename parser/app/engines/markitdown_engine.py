"""MarkItDown 引擎：Word、PPT 的快速解析（知识库技术方案 6.2、需求 4.1.4）。

先用 MarkItDown 把 docx、pptx 转成 Markdown，再用 Docling 的 Markdown 后端
（``docling.backend.md_backend``）把 Markdown 转成 DoclingDocument（ADR 0005）。

- Word：保留 MarkItDown 输出的标题层级，没有页码（需求 4.1.8），元素不设 ``prov``。
- PPT：MarkItDown 在每张幻灯片前输出 ``<!-- Slide number: N -->``，按这个标记分页，
  用标记里的数字给该页的元素补上页码；Markdown 没有坐标，包围框留空。
- 旧格式（doc、rtf、odt、ppt、odp）由 worker 先调用 ``/v1/convert``（X02）转成
  docx、pptx，这里收到时返回 ``UNSUPPORTED_TYPE``。

Markdown 转 DoclingDocument 由 ``markdown_doc.py``（X13 的共用模块）完成，
标题级别在那里统一：``#`` 是 1 级 ``section_header``。

Docling 的 Markdown 后端不抓取本地和远程资源（``fetch_images``、
``enable_local_fetch``、``enable_remote_fetch`` 都是 False），Markdown 里的图片
只保留图片元素，不下载图片内容。
"""

from __future__ import annotations

import re
from pathlib import Path

import pptx
from docling_core.types.doc.document import DoclingDocument
from markitdown import MarkItDown, MissingDependencyException
from markitdown.converters import DocxConverter, PptxConverter

from .. import doctools
from ..errors import ParseError
from ..filetypes import FILE_TYPES
from .base import ParseContext, ParseOutput
from .markdown_doc import markdown_to_document

__all__ = [
    "MarkitdownEngine",
    "parse_docx",
    "parse_pptx",
    "split_slides",
]

ENGINE_VERSION = "markitdown-0.1"

# 本引擎实际解析的扩展名；同类型的旧格式由 worker 先转换（X02）
NATIVE_EXTS = {"word": "docx", "ppt": "pptx"}

# MarkItDown（锁定 0.1.9）在每张幻灯片前输出的页标记
_SLIDE_MARKER = re.compile(r"^<!--\s*Slide number:\s*(\d+)\s*-->\s*$", re.MULTILINE)

# 幻灯片尺寸的 EMU 到点换算：1 点 = 12700 EMU
_EMU_PER_POINT = 12700


class MarkitdownEngine:
    """快速档 Word、PPT 引擎：MarkItDown 转 Markdown，Docling 的 Markdown 后端转文档模型。"""

    name = "markitdown"

    def supports(self, file_type: str, ext: str) -> bool:
        # 和内置引擎处理 excel 一样，在类型层面支持：doc、ppt 等旧格式由 worker
        # 先转成 docx、pptx（X02），类型不支持时引擎不会出现在 /v1/engines 里
        return file_type in NATIVE_EXTS and ext in FILE_TYPES[file_type]

    def availability(self) -> tuple[bool, str | None]:
        # MarkItDown 和 Docling 随依赖安装，无需外部部署
        return True, None

    def parse(self, ctx: ParseContext) -> ParseOutput:
        expected = NATIVE_EXTS.get(ctx.file_type)
        if expected is None:
            raise ParseError("UNSUPPORTED_TYPE", f"MarkItDown 引擎不支持 {ctx.file_type} 类型")
        if ctx.ext != expected:
            raise ParseError("UNSUPPORTED_TYPE", f"请先将文件转换为 {expected} 后再解析")
        if expected == "docx":
            return parse_docx(ctx)
        return parse_pptx(ctx)


def to_markdown(path: str) -> str:
    """用 MarkItDown 把本地文件转成 Markdown。

    只注册 docx、pptx 两个转换器：MarkItDown 自带的纯文本、压缩包等兜底转换器会把
    损坏的 docx、pptx 当纯文本转出乱码，这里不要它们，损坏文件直接报错。不加载第三方
    插件，输入是 worker 下载到临时目录的本地文件，不访问网络。
    """
    converter = MarkItDown(enable_builtins=False)
    converter.register_converter(DocxConverter())
    converter.register_converter(PptxConverter())
    try:
        return converter.convert(path).markdown
    except MissingDependencyException as exc:
        raise ParseError("ENGINE_UNAVAILABLE", "解析引擎缺少 Word、PPT 解析所需的依赖") from exc
    except Exception as exc:  # noqa: BLE001 - 转不出来一律按文件损坏处理
        raise ParseError("FILE_CORRUPTED") from exc


def set_page_numbers(document: DoclingDocument, page_no: int) -> None:
    """给文档里所有元素补上页码；Markdown 没有坐标，包围框留空。"""
    for item, _level in document.iterate_items():
        if not hasattr(item, "prov"):
            continue
        text = getattr(item, "text", "") or ""
        item.prov = [doctools.provenance(page_no, None, charspan=(0, len(text)))]


def split_slides(markdown: str) -> list[tuple[int, str]]:
    """按 MarkItDown 的页标记切分 Markdown，返回 (页码, 该页的 Markdown)。"""
    parts = _SLIDE_MARKER.split(markdown)
    return [(int(parts[i]), parts[i + 1].strip()) for i in range(1, len(parts), 2)]


def slide_size(path: str) -> tuple[float, float]:
    """从 pptx 读幻灯片尺寸，换算成点（Markdown 里没有尺寸信息）。"""
    presentation = pptx.Presentation(path)
    return (
        presentation.slide_width / _EMU_PER_POINT,
        presentation.slide_height / _EMU_PER_POINT,
    )


def parse_docx(ctx: ParseContext) -> ParseOutput:
    """解析 Word：标题层级来自 MarkItDown 输出的 Markdown，没有页码。"""
    markdown = to_markdown(ctx.input_path)
    document = markdown_to_document(markdown, Path(ctx.input_path).stem)
    ctx.report(1, 1)
    return ParseOutput(
        document=document,
        pages=0,
        ocr_pages=0,
        engine_version=ENGINE_VERSION,
    )


def parse_pptx(ctx: ParseContext) -> ParseOutput:
    """解析 PPT：每张幻灯片一个文档，合并后每个元素带上所在页码。"""
    slides = split_slides(to_markdown(ctx.input_path))
    document = doctools.new_document(Path(ctx.input_path).stem)
    width, height = slide_size(ctx.input_path)
    for index, (page_no, markdown) in enumerate(slides, start=1):
        doctools.add_page(document, page_no, width, height)
        slide = markdown_to_document(markdown, f"slide-{page_no}")
        set_page_numbers(slide, page_no)
        document.add_document(slide)
        ctx.report(index, len(slides))
    return ParseOutput(
        document=document,
        pages=len(slides),
        ocr_pages=0,
        engine_version=ENGINE_VERSION,
    )
