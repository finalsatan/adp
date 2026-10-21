from __future__ import annotations

import re

from ..builder import ElementBuilder

_PARAGRAPH_SPLIT = re.compile(r"\n[ \t]*\n+")


def parse_txt(text: str, builder: ElementBuilder) -> None:
    """Plain text: blank-line separated paragraphs, no invented structure."""

    blocks = [block.strip() for block in _PARAGRAPH_SPLIT.split(text) if block.strip()]
    for index, block in enumerate(blocks, start=1):
        builder.add("paragraph", block, paragraph=index)
