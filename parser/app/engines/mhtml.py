"""MHTML 容器拆包：只保留 HTML 正文与归档内嵌资源，不解析远程引用。"""

from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from ..errors import ParseError


class _RemoteResourceStripper(HTMLParser):
    """移除脚本及远程资源地址，避免 HTML 解析阶段访问网络。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.script_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "script":
            self.script_depth += 1
            return
        if self.script_depth:
            return
        clean = []
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on") or name == "style":
                continue
            if (
                name in {"src", "href", "poster", "background"}
                and value
                and value.strip().lower().startswith(("http:", "https:", "//"))
            ):
                continue
            clean.append((name, value))
        self.parts.append("<" + tag + "".join(_attr(k, v) for k, v in clean) + ">")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self.script_depth:
            self.script_depth -= 1
            return
        if not self.script_depth:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.script_depth:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self.script_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.script_depth:
            self.parts.append(f"&#{name};")


def _attr(name: str, value: str | None) -> str:
    if value is None:
        return f" {name}"
    escaped = value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return f' {name}="{escaped}"'


def extract_mhtml(path: str, output_path: str) -> Path:
    """提取归档 HTML 并写入安全化的临时 HTML 文件。"""
    try:
        message = BytesParser(policy=policy.default).parsebytes(Path(path).read_bytes())
        html_part = next(
            (part for part in message.walk() if part.get_content_type() == "text/html"), None
        )
        if html_part is None:
            raise ParseError("FILE_CORRUPTED", "MHTML 文件不包含 HTML 正文")
        html = html_part.get_content()
        if not isinstance(html, str):
            raise ParseError("FILE_CORRUPTED", "MHTML HTML 正文编码无效")
        resources: dict[str, str] = {}
        for part in message.walk():
            if part is html_part or part.get_content_maintype() == "multipart":
                continue
            content_id = part.get("Content-ID")
            payload = part.get_payload(decode=True)
            if content_id and payload:
                mime = part.get_content_type()
                data = base64.b64encode(payload).decode("ascii")
                resources[content_id.strip("<>")] = f"data:{mime};base64,{data}"
        for content_id, data_uri in resources.items():
            html = html.replace("cid:" + content_id, data_uri)
        parser = _RemoteResourceStripper()
        parser.feed(html)
        parser.close()
        target = Path(output_path)
        target.write_text("".join(parser.parts), encoding="utf-8")
        return target
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError("FILE_CORRUPTED", "MHTML 文件解析失败") from exc
