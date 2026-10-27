"""把样例文档发给本地 mineru 服务，重新生成快照样例（只在更新样例时运行）。

先启动本地服务（``make parser-dev-up``，宿主机端口 59003），然后：

    MINERU_URL=http://127.0.0.1:59003 MINERU_API_KEY=adpdev \
        uv run python tests/engines/mineru/samples/refresh.py

它会调用 ``app.engines.mineru`` 的客户端（和适配器走同一条路径），把每个样例的
``middle_json.json`` 和 ``images/`` 原样写到 ``<类型>/`` 下，供快照测试使用。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[3]))

from app.engines.mineru.client import MineruClient  # noqa: E402

SAMPLES = (
    ("sample.pdf", "pdf", "basic"),
    ("sample.docx", "docx", "flash"),
    ("sample.pptx", "pptx", "flash"),
)


def main() -> None:
    base_url = os.environ.get("MINERU_URL", "")
    if not base_url:
        raise SystemExit("请设置 MINERU_URL（本地开发默认 http://127.0.0.1:59003）")
    client = MineruClient(base_url, os.environ.get("MINERU_API_KEY"), poll_interval=1.0)
    available, reason = client.health()
    if not available:
        raise SystemExit(f"mineru 服务不可用：{reason}")

    for filename, target, tier in SAMPLES:
        artifacts = client.parse(HERE / filename, tier=tier)
        directory = HERE / target
        if directory.exists():
            shutil.rmtree(directory)
        (directory / "images").mkdir(parents=True)
        (directory / "middle_json.json").write_text(
            json.dumps(artifacts.middle_json, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        for name, data in artifacts.images.items():
            (directory / "images" / Path(name).name).write_bytes(data)
        print(f"{filename} -> {target}/ (tier={artifacts.tier}, {len(artifacts.images)} images)")


if __name__ == "__main__":
    main()
