from __future__ import annotations


class ParserError(Exception):
    """An error that can be returned to the caller as a readable JSON error."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_payload(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


class ScannedPdfError(ParserError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            "scanned_pdf_no_text",
            message
            or (
                "PDF 未包含可提取的文本层，可能是扫描件或纯图片 PDF。"
                "请上传带文本层的文档；扫描件需 OCR，当前版本未内置。"
            ),
            status_code=422,
        )
