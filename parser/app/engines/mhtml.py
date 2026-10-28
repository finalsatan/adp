"""MHTML 容器拆包：只保留 HTML 正文与归档内嵌资源，不解析远程引用。"""

from __future__ import annotations

import base64
import re
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from ..errors import ParseError
from .builtin_text import read_text

# <meta charset="gbk"> 或 <meta http-equiv="Content-Type" content="text/html; charset=gbk">
_CHARSET = re.compile(rb"""<meta[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
_CHARSET_IN_CONTENT = re.compile(r"charset\s*=\s*[A-Za-z0-9_.:-]+", re.IGNORECASE)


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
            if tag == "meta" and value:
                # 输出统一是 UTF-8，原文的编码声明要跟着改，否则 Docling 会按旧声明再解一次（X17）
                if name == "charset":
                    value = "utf-8"
                elif name == "content":
                    value = _CHARSET_IN_CONTENT.sub("charset=utf-8", value)
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


def declared_charset(raw: bytes) -> str | None:
    """从 HTML 前 4KB 的 ``<meta>`` 里取声明的编码。"""
    match = _CHARSET.search(raw[:4096])
    return match.group(1).decode("ascii") if match else None


def read_html(raw: bytes) -> str:
    """按 BOM、严格 UTF-8、文件里声明的编码、自动识别的顺序读取 HTML（X17）。"""
    return read_text(raw, declared_charset(raw))


def write_safe_html(html: str, output_path: str | Path) -> Path:
    """去掉脚本和远程资源引用，按 UTF-8 写入临时 HTML 文件。"""
    parser = _RemoteResourceStripper()
    parser.feed(html)
    parser.close()
    target = Path(output_path)
    target.write_text("".join(parser.parts), encoding="utf-8")
    return target


def extract_mhtml(path: str, output_path: str) -> Path:
    """提取归档 HTML 并写入安全化的临时 HTML 文件。"""
    try:
        message = BytesParser(policy=policy.default).parsebytes(Path(path).read_bytes())
        html_part = next(
            (part for part in message.walk() if part.get_content_type() == "text/html"), None
        )
        if html_part is None:
            raise ParseError("FILE_CORRUPTED", "MHTML 文件不包含 HTML 正文")
        # 不交给 email 的 get_content() 解码：MIME 头没有 charset 时它按 ASCII 解，中文全变成
        # 替换字符。改用原始字节走解析时同一套编码识别；MIME 头没有声明时再看正文的
        # <meta>（X17 复审）。
        payload = html_part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise ParseError("FILE_CORRUPTED", "MHTML HTML 正文编码无效")
        html = read_text(payload, html_part.get_content_charset() or declared_charset(payload))
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
        return write_safe_html(html, output_path)
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError("FILE_CORRUPTED", "MHTML 文件解析失败") from exc
