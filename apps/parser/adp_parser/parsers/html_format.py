from __future__ import annotations

import re
import warnings

from bs4 import (
    BeautifulSoup,
    Comment,
    MarkupResemblesLocatorWarning,
    NavigableString,
    Tag,
)

from ..builder import ElementBuilder, table_row_text

_IGNORED_TAGS = {
    "head",
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "object",
    "embed",
}
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_CONTAINER_TAGS = {
    "html",
    "body",
    "div",
    "section",
    "article",
    "main",
    "header",
    "footer",
    "aside",
    "nav",
    "figure",
    "figcaption",
    "form",
    "fieldset",
    "details",
    "summary",
    "dl",
    "dt",
    "dd",
}


def parse_html(html_text: str, builder: ElementBuilder) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        soup = BeautifulSoup(html_text, "html.parser")

    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()

    root = soup.body or soup
    _HtmlWalker(builder).walk(root)


class _HtmlWalker:
    def __init__(self, builder: ElementBuilder) -> None:
        self.builder = builder
        self.paragraph_index = 0
        self.table_group = 0

    def walk(self, container: Tag, list_depth: int = 0) -> None:
        buffer: list[str] = []

        def flush() -> None:
            if not buffer:
                return
            text = _clean(" ".join(buffer))
            buffer.clear()
            if text:
                self.paragraph_index += 1
                self.builder.add("paragraph", text, paragraph=self.paragraph_index)

        for child in container.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                buffer.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue

            name = (child.name or "").lower()
            if name in _IGNORED_TAGS:
                continue

            if name in _HEADING_TAGS:
                flush()
                text = _clean(child.get_text(" "))
                if text:
                    self.builder.add_heading(text, _HEADING_TAGS[name])
                continue

            if name == "p":
                flush()
                text = _clean(child.get_text(" "))
                if text:
                    self.paragraph_index += 1
                    self.builder.add("paragraph", text, paragraph=self.paragraph_index)
                continue

            if name in {"ul", "ol"}:
                flush()
                self._walk_list(child, list_depth)
                continue

            if name == "li":
                flush()
                self._handle_list_item(child, list_depth)
                continue

            if name == "table":
                flush()
                self._handle_table(child)
                continue

            if name == "pre":
                flush()
                code = child.get_text().strip("\n")
                if code.strip():
                    self.paragraph_index += 1
                    self.builder.add("code", code, paragraph=self.paragraph_index)
                continue

            if name == "blockquote":
                flush()
                text = _clean(child.get_text(" "))
                if text:
                    self.paragraph_index += 1
                    self.builder.add("paragraph", text, paragraph=self.paragraph_index)
                continue

            if name in {"br", "hr"}:
                flush()
                continue

            if name == "img":
                alt = _clean(child.get("alt", ""))
                if alt:
                    buffer.append(alt)
                continue

            if name in _CONTAINER_TAGS:
                flush()
                self.walk(child, list_depth)
                continue

            # Inline/unknown tag: keep its text and continue collecting.
            text = child.get_text(" ")
            if text:
                buffer.append(text)

        flush()

    def _walk_list(self, list_tag: Tag, list_depth: int) -> None:
        for child in list_tag.find_all(["li", "ul", "ol"], recursive=False):
            if child.name.lower() == "li":
                self._handle_list_item(child, list_depth)
            else:
                self._walk_list(child, list_depth + 1)

    def _handle_list_item(self, item: Tag, list_depth: int) -> None:
        text = _clean(_text_without_nested(item))
        if text:
            self.paragraph_index += 1
            self.builder.add(
                "list_item",
                text,
                level=min(9, list_depth + 1),
                paragraph=self.paragraph_index,
            )
        for nested in item.find_all(["ul", "ol", "table"], recursive=False):
            if nested.name.lower() == "table":
                self._handle_table(nested)
            else:
                self._walk_list(nested, list_depth + 1)

    def _handle_table(self, table: Tag) -> None:
        rows = table.find_all("tr")
        if not rows:
            return
        caption = table.find("caption", recursive=False)
        if caption is not None:
            caption_text = _clean(caption.get_text(" "))
            if caption_text:
                self.paragraph_index += 1
                self.builder.add(
                    "paragraph", caption_text, paragraph=self.paragraph_index
                )

        self.table_group += 1
        for row_index, row in enumerate(rows, start=1):
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells:
                continue
            cell_texts = [_clean(cell.get_text(" ")) for cell in cells]
            if not any(cell_texts):
                continue
            in_thead = row.find_parent("thead") is not None
            has_th = bool(row.find_all("th", recursive=False))
            element_type = (
                "table_header"
                if row_index == 1 or in_thead or has_th
                else "table_row"
            )
            self.builder.add(
                element_type,
                table_row_text(cell_texts),
                row=row_index,
                table_group=self.table_group,
            )


def _text_without_nested(tag: Tag) -> str:
    parts: list[str] = []
    for text_node in tag.find_all(string=True):
        if isinstance(text_node, Comment):
            continue
        ancestor = text_node.parent
        skip = False
        while ancestor is not None and ancestor is not tag:
            if (ancestor.name or "").lower() in {"ul", "ol", "table"}:
                skip = True
                break
            ancestor = ancestor.parent
        if not skip:
            parts.append(str(text_node))
    return " ".join(parts)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
