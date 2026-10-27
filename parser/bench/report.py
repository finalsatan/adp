"""生成不依赖外部资源的对比报告。"""

from __future__ import annotations

import html
import re
from collections import defaultdict
from pathlib import Path

from markdown_it import MarkdownIt


def write_report(rows, root: Path):
    escape = lambda value: html.escape(str(value))  # noqa: E731
    parts = [
        '<!doctype html><meta charset="utf-8"><title>解析对比</title>',
        "<style>body{font:14px sans-serif;margin:2em}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:.5em;vertical-align:top}"
        ".columns{display:flex;gap:1em;overflow:auto}.column{min-width:360px;flex:1}"
        ".content{border:1px solid #ddd;padding:1em;overflow:auto}img{max-width:100%}"
        "</style><h1>解析对比</h1><h2>汇总</h2>",
        "<table><tr><th>引擎</th><th>类型</th><th>成功率</th><th>平均每页耗时（秒）</th><th>内存峰值（MB）</th></tr>",
    ]
    groups = defaultdict(list)
    by_file = defaultdict(list)
    for row in rows:
        groups[row["engine"], row["type"]].append(row)
        by_file[row["file"]].append(row)
    for (engine, kind), group in sorted(groups.items()):
        success = [r for r in group if r["status"] == "成功"]
        pages = sum(r.get("pages", 0) for r in success)
        duration = sum(r.get("duration_s", 0) for r in success if r.get("pages", 0))
        memories = [
            r["peak_memory_mb"] for r in group if isinstance(r.get("peak_memory_mb"), (int, float))
        ]
        memory = (
            max(memories)
            if memories
            else ("见 mineru 容器" if engine.startswith("mineru-") else "—")
        )
        parts.append(
            "<tr>"
            + "".join(
                f"<td>{escape(x)}</td>"
                for x in (
                    engine,
                    kind,
                    f"{len(success)}/{len(group)}",
                    round(duration / pages, 3) if pages else "—",
                    memory,
                )
            )
            + "</tr>"
        )
    parts.append("</table>")
    markdown = MarkdownIt("commonmark", {"html": False}).enable("table")
    for filename, group in by_file.items():
        parts.append(f'<h2>{escape(filename)}</h2><div class="columns">')
        for row in group:
            stats = "，".join(
                f"{label}: {row.get(key, '—')}"
                for key, label in [
                    ("status", "状态"),
                    ("error_code", "错误码"),
                    ("pages", "页数"),
                    ("ocr_pages", "OCR 页"),
                    ("headings", "标题"),
                    ("tables", "表格"),
                    ("images", "图片"),
                    ("characters", "字数"),
                    ("duration_s", "耗时（秒）"),
                    ("conversion_s", "转换（秒）"),
                    ("warmup_s", "加载（秒）"),
                    ("peak_memory_mb", "峰值内存（MB）"),
                ]
            )
            doc = root / row["document"] / "doc.md"
            content = (
                markdown.render(doc.read_text(encoding="utf-8"))
                if row["status"] == "成功" and doc.exists()
                else escape(row.get("error", row["status"]))
            )
            # 文档中的远程图片不会在离线报告中发出网络请求。
            content = re.sub(r'<img\s+[^>]*src="(?:https?:)?//[^>]*>', "", content, flags=re.I)
            # 图片引用相对于各自的导出目录。
            content = content.replace('src="images/', f'src="{escape(row["document"])}/images/')
            parts.append(
                f'<section class="column"><h3>{escape(row["engine"])}</h3>'
                f'<p>{escape(stats)}</p><div class="content">{content}</div></section>'
            )
        parts.append("</div>")
    (root / "report.html").write_text("\n".join(parts), encoding="utf-8")
