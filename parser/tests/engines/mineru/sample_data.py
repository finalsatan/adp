"""快照样例的读写：MinerU 实际输出的 middle_json 和图片。

样例用 ``samples/refresh.py`` 从本地 mineru 服务重新生成（见卡片 X08）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SAMPLES_DIR = Path(__file__).parent / "samples"


@dataclass
class Sample:
    """一份样例：middle_json、图片（ZIP 里的 images/）和源文件。"""

    kind: str
    middle_json: dict
    images: dict[str, bytes]
    source: Path

    @property
    def directory(self) -> Path:
        return SAMPLES_DIR / self.kind


def load_sample(kind: str) -> Sample:
    directory = SAMPLES_DIR / kind
    middle = json.loads((directory / "middle_json.json").read_text(encoding="utf-8"))
    images = {
        f"images/{path.name}": path.read_bytes()
        for path in sorted((directory / "images").iterdir())
        if path.is_file()
    }
    return Sample(
        kind=kind, middle_json=middle, images=images, source=SAMPLES_DIR / f"sample.{kind}"
    )


def page_blocks(sample: Sample) -> list[dict]:
    """按页展开所有顶层块。"""
    blocks = []
    for page in sample.middle_json["pages"]:
        for block in page["blocks"]:
            blocks.append(block)
    return blocks


def find_block(sample: Sample, block_type: str) -> dict:
    for block in page_blocks(sample):
        if block["type"] == block_type:
            return block
    raise AssertionError(f"样例里没有 {block_type} 块")
