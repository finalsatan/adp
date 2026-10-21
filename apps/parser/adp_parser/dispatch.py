from __future__ import annotations

from .builder import ElementBuilder, ParseResult
from .config import Settings
from .decoding import decode_text_bytes
from .errors import ParserError
from .parsers.csv_format import parse_csv
from .parsers.docx_format import parse_docx
from .parsers.html_format import parse_html
from .parsers.markdown import parse_markdown
from .parsers.pdf_format import parse_pdf
from .parsers.pptx_format import parse_pptx
from .parsers.txt import parse_txt
from .parsers.xlsx_format import parse_xlsx
from .validation import (
    TEXT_EXTENSIONS,
    decode_base64_strict,
    normalize_extension,
    validate_content,
    validate_mime,
)


def parse_request(
    *,
    file_name: str,
    mime: str | None,
    data_base64: str | None,
    text: str | None,
    settings: Settings,
) -> ParseResult:
    extension = normalize_extension(file_name)
    validate_mime(mime, extension)

    if text is not None and data_base64 is not None:
        raise ParserError(
            "invalid_request",
            "dataBase64 与 text 只能提供一个，不能同时提供。",
            400,
        )
    if text is None and data_base64 is None:
        raise ParserError(
            "invalid_request",
            "必须提供 dataBase64 或 text 之一。",
            400,
        )

    limits = settings.limits
    if text is not None:
        if extension not in TEXT_EXTENSIONS:
            raise ParserError(
                "text_input_not_supported",
                f"{extension} 是二进制格式，不能用 text 字段提交；请使用 dataBase64。",
                400,
            )
        if "\x00" in text:
            raise ParserError(
                "binary_content",
                "text 字段包含 NUL 字符，不能按纯文本解析。",
                415,
            )
        data = text.encode("utf-8")
        if len(data) > limits.max_input_bytes:
            raise ParserError(
                "file_too_large",
                f"文本大小超过 {limits.max_input_bytes // (1024 * 1024)}MB 上限。",
                413,
            )
        validate_content(data, extension, limits)
        text_content: str | None = text
    else:
        assert data_base64 is not None
        data = decode_base64_strict(data_base64, limits.max_input_bytes)
        validate_content(data, extension, limits)
        text_content = None

    builder = ElementBuilder(limits)
    engine = _parse_by_extension(
        extension,
        data,
        text_content,
        builder,
        settings,
        file_name=file_name,
        mime=mime,
    )
    result = builder.result(engine)
    if not result.elements or not result.text.strip():
        raise ParserError(
            "no_extractable_text",
            _empty_message(extension),
            422,
        )
    return result


def _parse_by_extension(
    extension: str,
    data: bytes,
    text_content: str | None,
    builder: ElementBuilder,
    settings: Settings,
    *,
    file_name: str,
    mime: str | None,
) -> str:
    limits = settings.limits
    if extension == ".pdf":
        return parse_pdf(
            data, builder, limits, file_name=file_name, mime=mime
        )
    if extension == ".docx":
        parse_docx(data, builder)
        return "python-docx"
    if extension == ".pptx":
        parse_pptx(data, builder, limits.max_slides)
        return "python-pptx"
    if extension == ".xlsx":
        parse_xlsx(data, builder, limits)
        return "openpyxl"

    if text_content is None:
        text_content = decode_text_bytes(data, extension, builder.warnings)

    if extension == ".txt":
        parse_txt(text_content, builder)
        return "plain-text"
    if extension == ".md":
        parse_markdown(text_content, builder, max_rows=limits.max_csv_rows)
        return "markdown-lite"
    if extension == ".csv":
        parse_csv(text_content, builder, limits.max_csv_rows)
        return "python-csv"
    if extension == ".html":
        parse_html(text_content, builder)
        return "beautifulsoup4"

    raise ParserError(
        "unsupported_file_type", f"不支持的文件扩展名 {extension}。", 415
    )


def _empty_message(extension: str) -> str:
    if extension in {".docx", ".pptx", ".xlsx"}:
        return (
            f"{extension} 未提取到可读文本。请上传带文本层的文档；"
            "扫描件或图片内容需 OCR，当前版本未内置。"
        )
    if extension == ".pdf":
        return (
            "PDF 未提取到可读文本。请上传带文本层的文档；"
            "扫描件需 OCR，当前版本未内置。"
        )
    return "文件未提取到可读文本内容。"
