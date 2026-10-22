"""测试用的假引擎，只有 ``PARSER_ENABLE_FAKE_ENGINE=1`` 时注册。

按 ``engine_params`` 可以控制生成的内容、进度、耗时和错误：

- ``pages``：页数，默认 2
- ``text``：正文，用空行分段，默认两段文字
- ``sleep_s``：每页报进度前睡眠的秒数，默认 0
- ``error_code``：报完进度后抛出该错误码，默认不抛
- ``error_message``：覆盖错误码对应的中文说明
"""

from __future__ import annotations

import time

from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from ..errors import ParseError, message_for
from ..filetypes import FILE_TYPES
from .base import ParseContext, ParseOutput

_DEFAULT_TEXT = "这是第一段测试文字。\n\n这是第二段测试文字。"


class FakeEngine:
    name = "fake"

    def supports(self, file_type: str, ext: str) -> bool:
        return file_type in FILE_TYPES

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def parse(self, ctx: ParseContext) -> ParseOutput:
        params = ctx.engine_params or {}
        pages = max(1, int(params.get("pages", 2)))
        text = str(params.get("text", _DEFAULT_TEXT))
        sleep_s = float(params.get("sleep_s", 0))
        error_code = params.get("error_code")

        document = DoclingDocument(name="fake")
        for page_no in range(1, pages + 1):
            document.add_page(page_no, Size(width=595, height=842))

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        for index, paragraph in enumerate(paragraphs):
            page_no = (index % pages) + 1
            prov = _provenance(page_no, paragraph)
            document.add_text(label=DocItemLabel.TEXT, text=paragraph, prov=prov)

        for page_no in range(1, pages + 1):
            if sleep_s:
                time.sleep(sleep_s)
            ctx.report(page_no, pages)

        if error_code:
            raise ParseError(error_code, params.get("error_message") or message_for(error_code))

        return ParseOutput(
            document=document,
            pages=pages,
            ocr_pages=0,
            engine_version="fake-0.1",
            empty_pages=[],
        )


def _provenance(page_no: int, text: str):
    from docling_core.types.doc.common.reference import ProvenanceItem

    return ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=72, t=100, r=523, b=72, coord_origin=CoordOrigin.BOTTOMLEFT),
        charspan=(0, len(text)),
    )
