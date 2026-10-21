from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_MAX_INPUT_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Limits:
    """Hard resource limits applied while parsing untrusted documents."""

    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES
    max_text_chars: int = 5_000_000
    max_elements: int = 100_000
    max_warnings: int = 100
    max_pdf_pages: int = 1_000
    max_slides: int = 1_000
    max_excel_rows: int = 50_000
    max_excel_columns: int = 512
    max_excel_cells: int = 250_000
    max_csv_rows: int = 100_000
    max_zip_entries: int = 4_096
    max_zip_entry_uncompressed: int = 100 * 1024 * 1024
    max_zip_uncompressed: int = 200 * 1024 * 1024
    max_zip_ratio: float = 300.0


@dataclass(frozen=True)
class Settings:
    token: str | None = None
    timeout_seconds: float = 60.0
    workers: int = 4
    limits: Limits = field(default_factory=Limits)

    @property
    def request_max_bytes(self) -> int:
        # base64 expands by 4/3; leave room for the JSON envelope and whitespace.
        return (self.limits.max_input_bytes * 4 // 3) + (1024 * 1024)

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("PARSER_TOKEN")
        token = token.strip() if token and token.strip() else None
        timeout = _env_float("PARSER_TIMEOUT_SECONDS", 60.0, minimum=0.1, maximum=3_600.0)
        workers = _env_int("PARSER_WORKERS", 4, minimum=1, maximum=32)
        max_input_mb = _env_int("PARSER_MAX_INPUT_MB", 20, minimum=1, maximum=100)
        max_text_chars = _env_int(
            "PARSER_MAX_TEXT_CHARS", 5_000_000, minimum=10_000, maximum=50_000_000
        )
        max_elements = _env_int(
            "PARSER_MAX_ELEMENTS", 100_000, minimum=1_000, maximum=1_000_000
        )
        limits = Limits(
            max_input_bytes=max_input_mb * 1024 * 1024,
            max_text_chars=max_text_chars,
            max_elements=max_elements,
        )
        return cls(token=token, timeout_seconds=timeout, workers=workers, limits=limits)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))
