"""内置文本解析（TXT）：charset-normalizer 识别编码，按空行分段。

知识库技术方案 6.2 的“内置文本解析”一行。TXT 没有页码，所有段落元素不设
``prov``（需求 4.1.8：没有页码的文档不提供跳页定位）。

编码识别覆盖 UTF-8、UTF-8 BOM、GBK、GB18030；charset-normalizer 对 GBK 通常
返回 gb18030（GBK 的超集），可以直接解码。识别不出或解码失败时按 GB18030、
UTF-8 依次兜底。
"""

from __future__ import annotations

import codecs
import contextlib
import re
from pathlib import Path

from charset_normalizer import from_bytes

from .. import doctools
from .base import ParseContext, ParseOutput

__all__ = [
    "decode_text",
    "detect_encoding",
    "normalize_encoding",
    "parse_text",
    "read_text",
    "split_paragraphs",
]

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


# GB2312、GBK 声明的文件常含超出声明字符集的字，按超集 GB18030 解码。
# codecs 不认 euc-cn、x-gbk 这些别名，也一并映射到 GB18030（X17 复审）。
_SUPERSETS = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "cp936": "gb18030",
    "ms936": "gb18030",
    "euc-cn": "gb18030",
    "euccn": "gb18030",
    "x-gbk": "gb18030",
    "csgb2312": "gb18030",
    "gb2312-80": "gb18030",
}
# 没有 BOM 就无法确定字节序，按没有声明处理
_BOM_REQUIRED = {"utf-16", "utf-32"}


def normalize_encoding(name: str | None) -> str | None:
    """把声明的编码规范化：别名先过 ``codecs.lookup``，GB2312、GBK 归到超集 GB18030。

    认不出的名字返回原样（解码时按 ``LookupError`` 落到自动识别），没有声明返回 ``None``。
    """
    if not name:
        return None
    name = name.strip().lower()
    if not name:
        return None
    with contextlib.suppress(LookupError):
        name = codecs.lookup(name).name
    if name in _BOM_REQUIRED:
        return None
    return _SUPERSETS.get(name, name)


def read_text(raw: bytes, declared: str | None = None) -> str:
    """读取 Markdown、HTML 等文本文件：BOM → 严格 UTF-8 → 声明的编码 → 自动识别（X17）。

    UTF-8 排在声明的编码之前：GB18030、latin-1 这类编码几乎不会解码失败，先按声明解会把
    实际是 UTF-8、声明却写错的网页解成乱码（X17 复审）。声明 utf-16 又没有 BOM 的按 UTF-8 处理。
    """
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig", errors="replace")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    encoding = normalize_encoding(declared)
    if encoding:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    return decode_text(raw)


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
