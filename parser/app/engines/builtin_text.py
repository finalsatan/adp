"""内置文本解析（TXT）：charset-normalizer 识别编码，按空行分段。

知识库技术方案 6.2 的“内置文本解析”一行。TXT 没有页码，所有段落元素不设
``prov``（需求 4.1.8：没有页码的文档不提供跳页定位）。

编码识别覆盖 UTF-8、UTF-8 BOM、GBK、GB18030；charset-normalizer 对 GBK 通常
返回 gb18030（GBK 的超集），可以直接解码。识别不出或解码失败时按 GB18030、
UTF-8 依次兜底。
"""

from __future__ import annotations

import codecs
import re
from pathlib import Path

from charset_normalizer import from_bytes

from .. import doctools
from .base import ParseContext, ParseOutput

__all__ = ["decode_text", "detect_encoding", "parse_text", "split_paragraphs"]

ENGINE_VERSION = "builtin-0.1"

# 空行：行内只有空白
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def detect_encoding(raw: bytes) -> str:
    """识别字节串的编码。UTF-8 BOM 单独处理，其余交给 charset-normalizer。"""
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    best = from_bytes(raw).best()
    if best is None or not best.encoding:
        return "utf-8"
    return best.encoding


def decode_text(raw: bytes) -> str:
    """按识别出的编码解码，失败时依次退回 GB18030、UTF-8。"""
    encoding = detect_encoding(raw)
    for candidate in (encoding, "gb18030", "utf-8"):
        try:
            return raw.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def split_paragraphs(text: str) -> list[str]:
    """按空行分段，去掉每段首尾空白，丢掉空段。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [part.strip() for part in _BLANK_LINE.split(text) if part.strip()]


def parse_text(ctx: ParseContext) -> ParseOutput:
    """解析 TXT：每个空行分隔的段落一个文字元素。"""
    text = decode_text(Path(ctx.input_path).read_bytes())
    document = doctools.new_document(Path(ctx.input_path).stem)
    for paragraph in split_paragraphs(text):
        doctools.add_paragraph(document, paragraph)
    ctx.report(1, 1)
    return ParseOutput(document=document, pages=0, engine_version=ENGINE_VERSION)
