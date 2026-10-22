"""文件类型代码与扩展名，平台各处统一使用这套代码（需求 4.1.3）。"""

from __future__ import annotations

# 类型代码 → 扩展名（不含点，小写）
FILE_TYPES: dict[str, tuple[str, ...]] = {
    "pdf": ("pdf",),
    "word": ("doc", "docx", "rtf", "odt"),
    "ppt": ("ppt", "pptx", "odp"),
    "excel": ("xls", "xlsx", "ods"),
    "csv": ("csv", "tsv"),
    "markdown": ("md",),
    "txt": ("txt",),
    "html": ("html", "htm", "shtml", "mhtml", "mht"),
    "epub": ("epub",),
    "ofd": ("ofd",),
    "image": ("png", "jpg", "jpeg", "bmp", "tiff", "webp", "gif", "jp2"),
}

# 扩展名 → 类型代码
EXT_TO_TYPE: dict[str, str] = {ext: code for code, exts in FILE_TYPES.items() for ext in exts}


def normalize_ext(ext: str) -> str:
    """去掉前导点并转小写。"""
    return ext.strip().lstrip(".").lower()


def type_for_ext(ext: str) -> str | None:
    return EXT_TO_TYPE.get(normalize_ext(ext))


def extensions(file_type: str) -> tuple[str, ...]:
    return FILE_TYPES.get(file_type, ())


def is_known_type(file_type: str) -> bool:
    return file_type in FILE_TYPES
