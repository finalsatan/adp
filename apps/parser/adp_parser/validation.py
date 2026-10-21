from __future__ import annotations

import base64
import binascii
import io
import re
import zipfile

from .config import Limits
from .errors import ParserError

MAX_FILENAME_LENGTH = 512

EXTENSION_ALIASES = {
    ".markdown": ".md",
    ".htm": ".html",
    ".text": ".txt",
}

SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".html", ".pdf", ".docx", ".pptx", ".xlsx"}

MIME_BY_EXTENSION = {
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/x-markdown", "text/plain"},
    ".csv": {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"},
    ".html": {"text/html", "application/xhtml+xml"},
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
}

GENERIC_MIMES = {"", "application/octet-stream", "binary/octet-stream"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".html"}
OFFICE_REQUIRED_ENTRY = {
    ".docx": "word/document.xml",
    ".pptx": "ppt/presentation.xml",
    ".xlsx": "xl/workbook.xml",
}

_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


def normalize_extension(file_name: str) -> str:
    """Return a supported lower-case extension without opening the name."""

    if not isinstance(file_name, str) or "\x00" in file_name:
        raise ParserError("invalid_file_name", "fileName 无效。", 400)
    if len(file_name) > MAX_FILENAME_LENGTH:
        raise ParserError("invalid_file_name", "fileName 过长（最多 512 字符）。", 400)

    # Only the basename is used to pick an extension.  The value is never
    # treated as a filesystem path and is never opened.
    base = file_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not base or base in {".", ".."}:
        raise ParserError("invalid_file_name", "fileName 不能为空。", 400)

    dot = base.rfind(".")
    if dot <= 0 or dot == len(base) - 1:
        raise ParserError(
            "unsupported_file_type",
            "无法从 fileName 判断文件格式。请提供带扩展名的文件名（支持 "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
            + "）。",
            415,
        )
    extension = base[dot:].lower()
    extension = EXTENSION_ALIASES.get(extension, extension)
    if extension not in SUPPORTED_EXTENSIONS:
        raise ParserError(
            "unsupported_file_type",
            f"不支持的文件扩展名 {extension!r}。支持："
            + ", ".join(sorted(SUPPORTED_EXTENSIONS)),
            415,
        )
    return extension


def validate_mime(mime: str | None, extension: str) -> None:
    if mime is None:
        return
    if not isinstance(mime, str):
        raise ParserError("invalid_mime", "mime 必须是字符串。", 400)
    normalized = mime.split(";", 1)[0].strip().lower()
    if normalized in GENERIC_MIMES:
        return
    allowed = MIME_BY_EXTENSION[extension]
    if normalized not in allowed:
        raise ParserError(
            "mime_mismatch",
            f"mime {mime!r} 与扩展名 {extension} 不匹配。该格式可接受的 mime："
            + ", ".join(sorted(allowed)),
            415,
        )


def validate_content(data: bytes, extension: str, limits: Limits) -> None:
    if not data:
        raise ParserError("empty_file", "文件内容为空。", 400)

    if extension == ".pdf":
        if b"%PDF-" not in data[:1024]:
            raise ParserError(
                "content_mismatch",
                "文件内容不是有效的 PDF（未找到 %PDF- 文件头）。",
                415,
            )
        return

    required_entry = OFFICE_REQUIRED_ENTRY.get(extension)
    if required_entry:
        _validate_office_zip(data, extension, required_entry, limits)


def _validate_office_zip(
    data: bytes, extension: str, required_entry: str, limits: Limits
) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_zip_entries:
                raise ParserError(
                    "zip_limit_exceeded",
                    f"压缩包条目数 {len(infos)} 超过上限 {limits.max_zip_entries}。",
                    413,
                )

            total_uncompressed = 0
            names: set[str] = set()
            for info in infos:
                name = info.filename
                names.add(name)
                if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
                    raise ParserError(
                        "unsafe_archive",
                        "Office 文件包含不安全的压缩包路径，已拒绝解析。",
                        400,
                    )
                if info.flag_bits & 0x1:
                    raise ParserError(
                        "unsafe_archive", "Office 文件包含加密条目，已拒绝解析。", 400
                    )
                if info.is_dir():
                    continue
                if info.file_size > limits.max_zip_entry_uncompressed:
                    raise ParserError(
                        "zip_limit_exceeded",
                        "Office 文件解压后的单个条目过大，已拒绝解析。",
                        413,
                    )
                total_uncompressed += info.file_size
                if total_uncompressed > limits.max_zip_uncompressed:
                    raise ParserError(
                        "zip_limit_exceeded",
                        f"Office 文件解压后总大小超过上限 "
                        f"{limits.max_zip_uncompressed // (1024 * 1024)}MB。",
                        413,
                    )
                if (
                    info.file_size > 1024 * 1024
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > limits.max_zip_ratio
                ):
                    raise ParserError(
                        "zip_limit_exceeded",
                        "Office 文件压缩比异常，疑似压缩炸弹，已拒绝解析。",
                        413,
                    )

            if required_entry not in names:
                raise ParserError(
                    "content_mismatch",
                    f"文件内容与 {extension} 扩展名不匹配（缺少 {required_entry}）。",
                    415,
                )

            # Office XML must not contain DTD/ENTITY declarations.  This blocks
            # external-entity style payloads before a parser library sees them.
            for name in names:
                if not (name.endswith(".xml") or name.endswith(".rels")):
                    continue
                with archive.open(name) as handle:
                    head = handle.read(8192).upper()
                if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
                    raise ParserError(
                        "unsafe_xml",
                        "Office 文件包含 DTD/ENTITY 声明，已拒绝解析。",
                        400,
                    )
    except ParserError:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        RuntimeError,
        OSError,
        EOFError,
        ValueError,
        KeyError,
    ):
        raise ParserError(
            "invalid_zip",
            f"文件内容不是有效的 {extension}（Office Open XML）压缩包。",
            415,
        )


def decode_base64_strict(value: str, max_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise ParserError("invalid_base64", "dataBase64 必须是字符串。", 400)

    raw = value.strip()
    if raw.lower().startswith("data:"):
        comma = raw.find(",")
        if comma < 0 or ";base64" not in raw[:comma].lower():
            raise ParserError(
                "invalid_base64", "data URL 必须是 base64 编码格式。", 400
            )
        raw = raw[comma + 1 :]

    raw = re.sub(r"\s+", "", raw)
    if not raw:
        raise ParserError("invalid_base64", "dataBase64 不能为空。", 400)
    if len(raw) % 4 != 0:
        raise ParserError(
            "invalid_base64",
            "dataBase64 不是严格合法的 base64（长度必须为 4 的倍数）。",
            400,
        )
    if not _BASE64_RE.fullmatch(raw):
        raise ParserError(
            "invalid_base64", "dataBase64 含有非法 base64 字符。", 400
        )
    if len(raw) > ((max_bytes + 2) // 3) * 4 + 4:
        raise ParserError(
            "file_too_large",
            f"解码后的文件超过 {max_bytes // (1024 * 1024)}MB 上限。",
            413,
        )

    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ParserError("invalid_base64", "dataBase64 解码失败。", 400) from exc

    canonical = base64.b64encode(data).decode("ascii").rstrip("=")
    if canonical != raw.rstrip("="):
        raise ParserError(
            "invalid_base64",
            "dataBase64 不是规范的 base64 编码（填充位或末尾字节不合法）。",
            400,
        )

    if len(data) > max_bytes:
        raise ParserError(
            "file_too_large",
            f"文件大小 {len(data)} 字节超过 {max_bytes // (1024 * 1024)}MB 上限。",
            413,
        )
    return data
