from __future__ import annotations

import html
import re

from ..builder import ElementBuilder, table_row_text

_ATX_HEADING = re.compile(r"^\s{0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$")
_SETEXT_UNDERLINE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])[ \t]+(.*)$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HR = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_TABLE_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")
_HTML_COMMENT = re.compile(r"<!--.*?-->")


def parse_markdown(text: str, builder: ElementBuilder, max_rows: int | None = None) -> None:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    paragraph_buffer: list[str] = []
    paragraph_index = 0
    table_group = 0

    def flush_paragraph() -> None:
        nonlocal paragraph_index
        if not paragraph_buffer:
            return
        raw = " ".join(part.strip() for part in paragraph_buffer if part.strip())
        paragraph_buffer.clear()
        cleaned = _inline_clean(raw)
        if cleaned:
            paragraph_index += 1
            builder.add("paragraph", cleaned, paragraph=paragraph_index)

    # Optional YAML front matter is metadata, not document content.
    if lines and lines[0].strip() == "---":
        end = None
        for candidate in range(1, len(lines)):
            if lines[candidate].strip() in {"---", "..."}:
                end = candidate
                break
        if end is not None:
            builder.warn("已跳过 Markdown 文件开头的 YAML front matter。")
            index = end + 1

    while index < len(lines):
        line = lines[index]

        fence_match = _FENCE.match(line)
        if fence_match:
            flush_paragraph()
            marker = fence_match.group(1)[0]
            fence_length = len(fence_match.group(1))
            index += 1
            code_lines: list[str] = []
            while index < len(lines):
                closing = re.match(rf"^\s{{0,3}}{re.escape(marker)}{{{fence_length},}}\s*$", lines[index])
                if closing:
                    index += 1
                    break
                code_lines.append(lines[index])
                index += 1
            code = "\n".join(code_lines).strip("\n")
            if code.strip():
                paragraph_index += 1
                builder.add("code", code, paragraph=paragraph_index)
            continue

        heading_match = _ATX_HEADING.match(line)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            heading_text = _inline_clean(heading_match.group(2) or "")
            if heading_text:
                builder.add_heading(heading_text, level)
            index += 1
            continue

        if (
            line.strip()
            and index + 1 < len(lines)
            and _SETEXT_UNDERLINE.match(lines[index + 1])
            and not _LIST_ITEM.match(line)
            and not _is_table_start(lines, index)
        ):
            flush_paragraph()
            underline = _SETEXT_UNDERLINE.match(lines[index + 1]).group(1)
            level = 1 if underline.startswith("=") else 2
            heading_text = _inline_clean(line)
            if heading_text:
                builder.add_heading(heading_text, level)
            index += 2
            continue

        if _is_table_start(lines, index):
            flush_paragraph()
            table_group += 1
            header_cells = _split_table_row(lines[index])
            index += 2  # header + separator
            row_number = 1
            builder.add(
                "table_header",
                table_row_text(header_cells),
                row=row_number,
                table_group=table_group,
            )
            while index < len(lines):
                candidate = lines[index]
                if not candidate.strip() or "|" not in candidate:
                    break
                if _FENCE.match(candidate) or _ATX_HEADING.match(candidate):
                    break
                row_number += 1
                if max_rows is not None and row_number > max_rows:
                    builder.warn(f"Markdown 表格行数超过 {max_rows} 行上限，后续行已截断。")
                    while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                        index += 1
                    break
                cells = _split_table_row(candidate)
                builder.add(
                    "table_row",
                    table_row_text(cells),
                    row=row_number,
                    table_group=table_group,
                )
                index += 1
            continue

        list_match = _LIST_ITEM.match(line)
        if list_match:
            flush_paragraph()
            base_indent = len(list_match.group(1).expandtabs(4))
            item_text = list_match.group(3)
            index += 1
            while index < len(lines):
                continuation = lines[index]
                if not continuation.strip():
                    break
                if _starts_block(lines, index):
                    break
                leading = len(continuation) - len(continuation.lstrip(" \t"))
                if leading <= base_indent:
                    break
                item_text += " " + continuation.strip()
                index += 1
            cleaned = _inline_clean(item_text)
            if cleaned:
                paragraph_index += 1
                level = min(9, base_indent // 2 + 1)
                builder.add(
                    "list_item", cleaned, level=level, paragraph=paragraph_index
                )
            continue

        if line.lstrip().startswith(">"):
            flush_paragraph()
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quote_lines.append(lines[index].lstrip()[1:].lstrip())
                index += 1
            quote = _inline_clean(" ".join(quote_lines))
            if quote:
                paragraph_index += 1
                builder.add("paragraph", quote, paragraph=paragraph_index)
            continue

        if not line.strip():
            flush_paragraph()
            index += 1
            continue

        if _HR.match(line):
            flush_paragraph()
            index += 1
            continue

        if _HTML_COMMENT.match(line.strip()):
            flush_paragraph()
            index += 1
            continue

        paragraph_buffer.append(line)
        index += 1

    flush_paragraph()


def _is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines) or "|" not in lines[index]:
        return False
    if "|" not in lines[index + 1]:
        return False
    separator_cells = _split_table_row(lines[index + 1])
    if len(separator_cells) < 2:
        return False
    return all(_TABLE_SEPARATOR_CELL.match(cell.replace(" ", "")) for cell in separator_cells)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    stripped = stripped.removeprefix("|")
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    cells = re.split(r"(?<!\\)\|", stripped)
    return [_inline_clean(cell.replace("\\|", "|")) for cell in cells]


def _starts_block(lines: list[str], index: int) -> bool:
    candidate = lines[index]
    return bool(
        _FENCE.match(candidate)
        or _ATX_HEADING.match(candidate)
        or _LIST_ITEM.match(candidate)
        or candidate.lstrip().startswith(">")
        or _HR.match(candidate)
        or _is_table_start(lines, index)
    )


def _inline_clean(text: str) -> str:
    text = _HTML_COMMENT.sub(" ", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", text)
    text = re.sub(r"<(?:https?|mailto):([^>]+)>", r"\1", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"`+([^`]*)`+", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    text = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", r"\1", text)
    text = html.unescape(text)
    return " ".join(text.split())
