"""内置解析引擎：TXT、CSV/TSV、Excel，以及 Docling 支持的 Markdown、HTML。"""

from __future__ import annotations

from ..errors import ParseError
from ..filetypes import FILE_TYPES
from .base import ParseContext, ParseOutput
from .builtin_table import parse_csv, parse_excel
from .builtin_text import parse_text

__all__ = ["BuiltinEngine"]


class BuiltinEngine:
    name = "builtin"

    def supports(self, file_type: str, ext: str) -> bool:
        if file_type == "txt":
            return ext == "txt"
        if file_type == "csv":
            return ext in ("csv", "tsv")
        if file_type == "excel":
            # xls、ods 由 worker 先转成 xlsx（X02），这里在类型层面支持 excel
            return ext in FILE_TYPES["excel"]
        if file_type == "markdown":
            return ext == "md"
        if file_type == "html":
            return ext in ("html", "htm", "shtml")
        return False

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def parse(self, ctx: ParseContext) -> ParseOutput:
        if ctx.file_type == "txt":
            return parse_text(ctx)
        if ctx.file_type == "csv":
            return parse_csv(ctx)
        if ctx.file_type == "excel":
            return parse_excel(ctx)
        if ctx.file_type in ("markdown", "html"):
            from .docling_engine import parse_native

            return parse_native(ctx)
        raise ParseError("UNSUPPORTED_TYPE", f"内置解析引擎不支持 {ctx.file_type} 类型")
