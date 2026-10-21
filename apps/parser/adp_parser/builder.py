from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .config import Limits

TABLE_TYPES = {"table_header", "table_row"}


@dataclass
class ParseResult:
    text: str
    elements: list[dict]
    engine: str
    warnings: list[str]

    def to_payload(self) -> dict:
        from . import SCHEMA_VERSION

        return {
            "schemaVersion": SCHEMA_VERSION,
            "text": self.text,
            "elements": self.elements,
            "engine": self.engine,
            "warnings": self.warnings,
        }


@dataclass
class HeadingStack:
    _entries: list[tuple[int, str]] = field(default_factory=list)

    def push(self, level: int, text: str) -> list[str]:
        level = max(1, min(level, 9))
        text = text.strip()
        if not text:
            return self.current()
        self._entries = [(lvl, value) for lvl, value in self._entries if lvl < level]
        self._entries.append((level, text))
        return self.current()

    def current(self) -> list[str]:
        return [value for _, value in self._entries]


class ElementBuilder:
    """Collects ordered elements, warnings and the flattened text."""

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.elements: list[dict] = []
        self.warnings: list[str] = []
        self.headings = HeadingStack()
        self._text_chars = 0
        self._group_ids: list[int | None] = []
        self._dropped_warning_added = False

    @property
    def at_element_limit(self) -> bool:
        return len(self.elements) >= self.limits.max_elements

    def warn(self, message: str) -> None:
        message = " ".join(str(message).split())
        if not message or message in self.warnings:
            return
        if len(self.warnings) >= self.limits.max_warnings:
            if not self._dropped_warning_added:
                self.warnings.append("警告数量过多，后续警告已省略。")
                self._dropped_warning_added = True
            return
        self.warnings.append(message)

    def _normalize(self, text: str) -> str:
        text = (
            str(text)
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\x0b", "\n")
            .replace("\x0c", "\n")
        )
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        return text.strip()

    def add(
        self,
        element_type: str,
        text: str,
        *,
        level: int | None = None,
        page: int | None = None,
        sheet: str | None = None,
        row: int | None = None,
        paragraph: int | None = None,
        heading_path: Iterable[str] | None = None,
        table_group: int | None = None,
    ) -> dict | None:
        text = self._normalize(text)
        if not text:
            return None

        if self.at_element_limit:
            self.warn(f"元素数量超过上限（{self.limits.max_elements}），后续内容已截断。")
            return None

        remaining = self.limits.max_text_chars - self._text_chars
        if remaining <= 0:
            self.warn(f"文本超过 {self.limits.max_text_chars} 字符上限，后续内容已截断。")
            return None
        if len(text) > remaining:
            text = text[:remaining]
            self.warn(f"文本超过 {self.limits.max_text_chars} 字符上限，末尾内容已截断。")

        order = len(self.elements) + 1
        element: dict = {
            "id": f"e{order}",
            "type": element_type,
            "text": text,
            "order": order,
        }
        if level is not None and level > 0:
            element["level"] = int(level)
        if page is not None:
            element["page"] = int(page)
        if sheet is not None:
            element["sheet"] = str(sheet)
        if row is not None:
            element["row"] = int(row)
        if paragraph is not None:
            element["paragraph"] = int(paragraph)

        path = list(heading_path) if heading_path is not None else self.headings.current()
        path = [part for part in path if part]
        if path:
            element["headingPath"] = path

        self.elements.append(element)
        self._group_ids.append(table_group if element_type in TABLE_TYPES else None)
        self._text_chars += len(text)
        return element

    def add_heading(
        self,
        text: str,
        level: int,
        *,
        page: int | None = None,
        sheet: str | None = None,
        paragraph: int | None = None,
    ) -> dict | None:
        text = self._normalize(text)
        if not text:
            return None
        path = self.headings.push(level, text)
        return self.add(
            "heading",
            text,
            level=level,
            page=page,
            sheet=sheet,
            paragraph=paragraph,
            heading_path=path,
        )

    def current_heading_path(self) -> list[str]:
        return self.headings.current()

    def reset_heading_path(self) -> None:
        self.headings._entries.clear()

    def flatten_text(self) -> str:
        parts: list[str] = []
        current_group: int | None | object = object()
        table_buffer: list[str] = []

        def flush_table() -> None:
            nonlocal table_buffer
            if table_buffer:
                parts.append("\n".join(table_buffer))
                table_buffer = []

        for element, group in zip(self.elements, self._group_ids):
            if element["type"] in TABLE_TYPES:
                if current_group is not group:
                    flush_table()
                    current_group = group
                table_buffer.append(element["text"])
            else:
                flush_table()
                current_group = object()
                parts.append(element["text"])
        flush_table()
        return "\n\n".join(part for part in parts if part)

    def result(self, engine: str) -> ParseResult:
        return ParseResult(
            text=self.flatten_text(),
            elements=self.elements,
            engine=engine,
            warnings=list(self.warnings),
        )


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if hasattr(value, "isoformat") and not isinstance(value, (str, bytes)):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", " ")
    return " ".join(text.split())


def table_row_text(cells: Iterable[object]) -> str:
    return " | ".join(clean_cell(cell) for cell in cells)
