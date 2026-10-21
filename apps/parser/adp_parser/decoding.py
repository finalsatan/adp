from __future__ import annotations

from bs4 import UnicodeDammit

from .errors import ParserError

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

_FALLBACK_ENCODINGS = ("utf-8-sig", "gb18030", "big5", "windows-1252")


def decode_text_bytes(data: bytes, extension: str, warnings: list[str]) -> str:
    """Decode text-like uploads.  The original bytes are never written to disk."""

    if not data:
        raise ParserError("empty_file", "文件内容为空。", 400)

    has_bom = any(data.startswith(bom) for bom, _ in _BOMS)
    binary_magic = (
        b"%PDF-",
        b"PK\x03\x04",
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff",
        b"GIF87a",
        b"GIF89a",
        b"RIFF",
        b"\x7fELF",
        b"\x1f\x8b",
    )
    if not has_bom and data.startswith(binary_magic):
        raise ParserError(
            "binary_content",
            "文件内容看起来是 PDF/Office/图片/压缩包等二进制格式，与文本扩展名不匹配。",
            415,
        )

    sample = data[:65536]
    if not has_bom:
        control_bytes = sum(
            1 for byte in sample if byte < 9 or byte in {11} or 14 <= byte <= 31
        )
        if b"\x00" in sample or (sample and control_bytes / len(sample) > 0.02):
            raise ParserError(
                "binary_content",
                "文件包含二进制控制字符，不能按纯文本解析；请检查文件扩展名或 mime。",
                415,
            )

    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                text = data.decode(encoding)
            except UnicodeDecodeError as exc:
                raise ParserError(
                    "invalid_encoding", f"无法按 {encoding} 解码文本文件。", 422
                ) from exc
            _warn_encoding(encoding, warnings)
            return _normalize_newlines(text)

    if extension == ".html":
        dammit = UnicodeDammit(
            data,
            ["utf-8", "gb18030", "big5", "windows-1252"],
            is_html=True,
        )
        if dammit.unicode_markup is not None:
            encoding = (dammit.original_encoding or "unknown").lower()
            _warn_encoding(encoding, warnings)
            return _normalize_newlines(dammit.unicode_markup)

    for encoding in _FALLBACK_ENCODINGS:
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        _warn_encoding(encoding, warnings)
        return _normalize_newlines(text)

    raise ParserError(
        "invalid_encoding",
        "无法识别文本编码。请将文件转换为 UTF-8 后重试。",
        422,
    )


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _warn_encoding(encoding: str, warnings: list[str]) -> None:
    normalized = encoding.lower().replace("_", "-")
    if normalized in {"utf-8", "utf-8-sig", "ascii"}:
        return
    message = f"文本未使用 UTF-8 编码（检测为 {encoding}），已按该编码解码。"
    if message not in warnings:
        warnings.append(message)
