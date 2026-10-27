"""Markdown 转 DoclingDocument 的共用实现，并统一标题级别（X13）。

Docling 的 Markdown 后端和 HTML 后端都把 1 级标题当成文档标题
（``DocItemLabel.TITLE``），其余级别减一（``##`` → 1 级）；而 Docling 原生解析
Word 时 Word 的标题 1 就是 1 级 ``section_header``。这里统一修正成
“``#``、``<h1>``、Word 标题 1 都是 1 级 ``section_header``”，修正后文档里不再
用 ``title`` 表示标题（知识库技术方案 6.2）。

Docling 引擎的 ``.md``、内置引擎的 markdown、MarkItDown 的 Word 和 PPT 都走
这个模块；Docling 原生解析 HTML（含 mhtml）的结果也用 ``normalize_heading_levels``
修正。MinerU 的标题级别由它自己的适配器处理（X08），不走这里。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docling.backend.md_backend import MarkdownDocumentBackend
from docling.datamodel.backend_options import MarkdownBackendOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument
from docling_core.types.doc.common.content_layer import ContentLayer
from docling_core.types.doc.document import DocItemLabel, DoclingDocument
from docling_core.types.doc.items.text import SectionHeaderItem

__all__ = [
    "markdown_file_to_document",
    "markdown_to_document",
    "normalize_heading_levels",
]


def normalize_heading_levels(document: DoclingDocument) -> None:
    """把 1 级标题从 ``title`` 改成 1 级 ``section_header``，其余标题级别加一。

    Docling 的 Markdown、HTML 后端按“1 级标题是文档标题”的约定输出（``#``、
    ``<h1>`` 是 ``title``，``##``、``<h2>`` 是 1 级 ``section_header``），这里
    换算成和 Word 原生解析一致的级别。furniture 内容层的 ``title`` 是文档元数据
    （HTML 的 ``<title>``），不算标题，留给后处理删除。

    ``TitleItem`` 没有 ``level`` 字段，用同样字段的 ``SectionHeaderItem`` 顶替；
    直接换 ``document.texts`` 里的对象，保留 ``self_ref``、父子和 ``children``
    引用（标题里的加粗等行内元素是它的子元素，用 ``replace_item`` 会被删掉）。
    """
    for index, item in enumerate(document.texts):
        if item.content_layer == ContentLayer.FURNITURE:
            continue
        if item.label == DocItemLabel.TITLE:
            document.texts[index] = SectionHeaderItem.model_validate(
                {**item.model_dump(), "label": DocItemLabel.SECTION_HEADER, "level": 1}
            )
        elif item.label == DocItemLabel.SECTION_HEADER:
            item.level = (item.level or 0) + 1


def markdown_to_document(markdown: str, name: str) -> DoclingDocument:
    """Markdown 文本转 DoclingDocument，并统一标题级别。"""
    return _convert(BytesIO(markdown.encode("utf-8")), name)


def markdown_file_to_document(path: str | Path, name: str) -> DoclingDocument:
    """Markdown 文件转 DoclingDocument，并统一标题级别。

    编码交给 Docling 的 ``decode_text``（先认 BOM，再 UTF-8、cp1252），和
    Docling 原生解析 ``.md`` 时一致。
    """
    return _convert(BytesIO(Path(path).read_bytes()), name)


def _convert(stream: BytesIO, name: str) -> DoclingDocument:
    """用 Docling 的 Markdown 后端转成 DoclingDocument（不抓取任何资源）。"""
    options = MarkdownBackendOptions(
        fetch_images=False,
        enable_local_fetch=False,
        enable_remote_fetch=False,
    )
    input_doc = InputDocument(
        path_or_stream=stream,
        format=InputFormat.MD,
        backend=MarkdownDocumentBackend,
        backend_options=options,
        filename=f"{name}.md",
    )
    backend = MarkdownDocumentBackend(
        in_doc=input_doc,
        path_or_stream=stream,
        options=options,
    )
    document = backend.convert()
    normalize_heading_levels(document)
    return document
