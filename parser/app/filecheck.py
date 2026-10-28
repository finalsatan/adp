"""解析前检查真实类型、压缩包目录和加密状态，不解压文档内容。"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import filetype
import olefile
import pypdfium2 as pdfium
from olefile.olefile import OleFileError
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .engines.builtin_text import read_text
from .errors import ParseError
from .filetypes import normalize_ext

TEXT_EXTS = {"txt", "md", "csv", "tsv", "html", "htm", "shtml", "mhtml", "mht"}
OOXML = {
    "docx": ("word/document.xml", "wordprocessingml.document.main+xml"),
    "xlsx": ("xl/workbook.xml", "spreadsheetml.sheet.main+xml"),
    "pptx": ("ppt/presentation.xml", "presentationml.presentation.main+xml"),
}
MIMETYPES = {
    "odt": b"application/vnd.oasis.opendocument.text",
    "ods": b"application/vnd.oasis.opendocument.spreadsheet",
    "odp": b"application/vnd.oasis.opendocument.presentation",
    "epub": b"application/epub+zip",
}
ENCRYPTED_MESSAGE = "文件已加密，请去掉密码后重新上传"
_MARKER_LIMIT = 1024 * 1024


class FileCheckSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    parser_archive_max_bytes: int = Field(default=1024**3, gt=0)


def _mismatch() -> None:
    raise ParseError("FILE_TYPE_MISMATCH")


def _looks_like_text(text: str) -> bool:
    """含 NUL 才判为不是文本（2026-10-28 用户决定 B）。

    Word 导出 txt 的手动换行 ``\\x0b``、DOS 文件尾 ``\\x1a``、日志里的 ANSI 转义
    ``\\x1b`` 都是正常文本里会出现的控制字符；而随机二进制里控制字符的占比也只有 10%
    左右，按占比判断既会误拒正常文件、又拦不住二进制，所以只看 NUL。
    """
    return "\x00" not in text


def _check_text(path: Path) -> None:
    """按解析时同一套编码识别解码，再看是不是文本（X17 复审）。

    以前这里自己排一套解码顺序，可能和真正解析时用的编码不一致。
    """
    raw = path.read_bytes()
    if not raw:
        return
    if not _looks_like_text(read_text(raw)):
        raise ParseError("FILE_CORRUPTED", "文件无法按常见文本编码读取，请检查文件内容和编码")


def _marker(archive: ZipFile, name: str) -> bytes:
    # 只读取少量标志文件；即使目录伪报大小也不会无限解压到内存。
    with archive.open(name) as handle:
        data = handle.read(_MARKER_LIMIT + 1)
    if len(data) > _MARKER_LIMIT:
        raise ParseError("ARCHIVE_TOO_LARGE")
    return data


def _check_zip(path: Path, ext: str, max_bytes: int) -> None:
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 10_000:
            raise ParseError("ARCHIVE_TOO_LARGE", "压缩包文件数量超过上限，已拒绝解析")
        if sum(item.file_size for item in entries) > max_bytes:
            raise ParseError("ARCHIVE_TOO_LARGE")
        if any(item.flag_bits & 1 for item in entries):
            raise ParseError("FILE_ENCRYPTED", ENCRYPTED_MESSAGE)
        names = set(archive.namelist())
        if ext in OOXML:
            main, content_type = OOXML[ext]
            if "[Content_Types].xml" not in names or main not in names:
                _mismatch()
            root = ElementTree.fromstring(_marker(archive, "[Content_Types].xml"))
            if not any(
                element.get("PartName") == f"/{main}"
                and element.get("ContentType")
                == f"application/vnd.openxmlformats-officedocument.{content_type}"
                for element in root
            ):
                _mismatch()
        elif ext in MIMETYPES:
            if "mimetype" not in names or _marker(archive, "mimetype").strip() != MIMETYPES[ext]:
                _mismatch()
            if ext == "epub":
                if "META-INF/container.xml" not in names:
                    _mismatch()
            else:
                if "content.xml" not in names:
                    _mismatch()
                if "META-INF/manifest.xml" in names:
                    root = ElementTree.fromstring(_marker(archive, "META-INF/manifest.xml"))
                    if any(item.tag.endswith("}encryption-data") for item in root.iter()):
                        raise ParseError("FILE_ENCRYPTED", ENCRYPTED_MESSAGE)
        elif ext == "ofd":
            if "OFD.xml" not in names:
                _mismatch()
        else:
            _mismatch()


def _check_ole(path: Path, ext: str) -> None:
    with olefile.OleFileIO(path) as archive:
        if archive.exists("EncryptedPackage") or archive.exists("EncryptionInfo"):
            raise ParseError("FILE_ENCRYPTED", ENCRYPTED_MESSAGE)
        streams = {
            "doc": ("WordDocument",),
            "ppt": ("PowerPoint Document",),
            "xls": ("Workbook", "Book"),
        }
        if not any(archive.exists(name) for name in streams.get(ext, ())):
            _mismatch()


def check_file(path: str | Path, ext: str, *, max_archive_bytes: int | None = None) -> None:
    """失败时抛 ParseError，供 runner 保留错误码和用户说明。"""
    path = Path(path)
    ext = normalize_ext(ext)
    try:
        if ext in TEXT_EXTS:
            _check_text(path)
            return
        with path.open("rb") as handle:
            header = handle.read(8192)
        kind = filetype.guess(header)
        if header.startswith(olefile.MAGIC):
            _check_ole(path, ext)
            return
        # filetype 可能进一步识别为 docx / odt，仍必须先检查整个压缩包目录。
        if header.startswith(b"PK\x03\x04") or (kind and kind.extension == "zip"):
            limit = (
                max_archive_bytes
                if max_archive_bytes is not None
                else FileCheckSettings().parser_archive_max_bytes
            )
            _check_zip(path, ext, limit)
            return
        actual = kind.extension if kind else None
        actual = {"tif": "tiff", "jpeg": "jpg", "jpx": "jp2"}.get(actual, actual)
        if actual != {"jpeg": "jpg"}.get(ext, ext):
            _mismatch()
        if ext == "pdf":
            try:
                with pdfium.PdfDocument(path) as document:
                    if not len(document):
                        raise ParseError("FILE_CORRUPTED")
                    page = document[0]
                    page.close()
            except pdfium.PdfiumError as exc:
                if exc.err_code == 4:  # FPDF_ERR_PASSWORD
                    raise ParseError("FILE_ENCRYPTED", ENCRYPTED_MESSAGE) from exc
                raise ParseError("FILE_CORRUPTED") from exc
    except ParseError:
        raise
    except (
        OSError,
        ValueError,
        IndexError,
        BadZipFile,
        ElementTree.ParseError,
        OleFileError,
    ) as exc:
        raise ParseError("FILE_CORRUPTED") from exc
