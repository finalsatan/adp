"""非 UTF-8 文本的解析、文本检查和错误码（X17）。"""

from __future__ import annotations

import base64
from pathlib import Path
from zipfile import BadZipFile

import pytest
from docling.exceptions import ConversionError
from PIL import Image

from app.engines.base import ParseContext, ParseOutput
from app.engines.builtin import BuiltinEngine
from app.engines.builtin_text import read_text
from app.engines.docling_engine import DoclingEngine
from app.engines.mineru.engine import MineruEngine
from app.errors import ParseError, message_for
from app.filecheck import check_file

BODY = "中文正文：知识库解析测试"
HEADING = "第一章 总则"


def context(path: Path, file_type: str, ext: str, **params) -> ParseContext:
    return ParseContext(
        input_path=str(path),
        file_type=file_type,
        ext=ext,
        engine_params=params,
        preprocess={},
        table={},
        work_dir=str(path.parent),
        report=lambda _done, _total: None,
    )


def all_text(output: ParseOutput) -> str:
    return "\n".join(item.text for item in output.document.texts)


def html_page(charset_meta: str) -> str:
    return f"<html><head>{charset_meta}</head><body><h1>{HEADING}</h1><p>{BODY}</p></body></html>"


def mhtml_archive(html: bytes, content_type: str, *, encoding: str = "base64") -> bytes:
    """拼一个只含 HTML 正文的 MHTML，正文编码由调用方决定。"""
    body = base64.encodebytes(html) if encoding == "base64" else html
    return (
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/related; boundary="b"\r\n\r\n'
        b"--b\r\n"
        + f"Content-Type: {content_type}\r\n".encode()
        + f"Content-Transfer-Encoding: {encoding}\r\n\r\n".encode()
        + body
        + b"\r\n--b--\r\n"
    )


def wrapped(inner: BaseException, message: str) -> BaseException:
    """模拟 Docling 把管线内部的错误包成 ``RuntimeError`` 并保留 ``__cause__``。"""
    try:
        raise inner
    except type(inner) as cause:
        try:
            raise RuntimeError(message) from cause
        except RuntimeError as outer:
            return outer


_BAD_ZIP_PIPELINE_ERROR = wrapped(BadZipFile("File is not a zip file"), "Pipeline failed")


@pytest.mark.parametrize(
    "meta",
    [
        '<meta charset="gbk">',
        '<meta http-equiv="Content-Type" content="text/html; charset=gb2312">',
        "",
    ],
)
def test_gbk_html_keeps_chinese(tmp_path, meta):
    path = tmp_path / "page.html"
    path.write_bytes(html_page(meta).encode("gbk"))
    for engine in (DoclingEngine(), BuiltinEngine()):
        text = all_text(engine.parse(context(path, "html", "html")))
        assert BODY in text
        assert HEADING in text
        assert "�" not in text


def test_utf8_html_without_declaration_unchanged(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(html_page(""), encoding="utf-8")
    text = all_text(DoclingEngine().parse(context(path, "html", "html")))
    assert BODY in text


def test_utf8_html_with_wrong_declaration_still_readable(tmp_path):
    """声明 gbk 但实际是 UTF-8：按声明解失败时退回 UTF-8。"""
    path = tmp_path / "page.html"
    path.write_text(html_page('<meta charset="gbk">'), encoding="utf-8")
    text = all_text(DoclingEngine().parse(context(path, "html", "html")))
    assert BODY in text


def test_utf8_html_with_gbk_declaration_that_decodes_is_still_read_as_utf8(tmp_path):
    """声明 gbk 但实际是 UTF-8，且 GB18030 能“解成功”的样例：必须优先试 UTF-8（X17 复审）。"""
    path = tmp_path / "page.html"
    page = f'<html><head><meta charset="gbk"></head><body><p>{BODY}</p></body></html>'
    raw = page.encode("utf-8")
    # 这个样例按 GB18030 解码不会报错，只会是乱码，所以旧顺序必然出错
    assert raw.decode("gb18030") != page
    path.write_bytes(raw)
    for engine in (DoclingEngine(), BuiltinEngine()):
        text = all_text(engine.parse(context(path, "html", "html")))
        assert BODY in text
        assert "\ufffd" not in text


@pytest.mark.parametrize("declared", ["gbk", "gb2312", "cp936", "euc-cn", "x-gbk", "csgb2312"])
def test_read_text_gbk_aliases_decode_gbk_bytes(declared):
    """GB2312/GBK 的别名都归到超集 GB18030，能解出 GBK 才有的字（X17 复审）。"""
    assert read_text("堃".encode("gbk"), declared) == "堃"


def test_read_text_prefers_utf8_over_declaration():
    """实际是 UTF-8、声明却写成 gbk：按 UTF-8 解，不能出乱码（X17 复审）。"""
    raw = "<p>中文正文</p>".encode()
    assert read_text(raw, "gbk") == "<p>中文正文</p>"


def test_read_text_ignores_utf16_declaration_without_bom():
    """声明 utf-16 但没有 BOM：按 UTF-8 处理，再退到自动识别（X17 复审）。"""
    assert read_text("<p>中文正文</p>".encode(), "utf-16") == "<p>中文正文</p>"
    assert "中文正文" in read_text("<p>中文正文</p>".encode("gbk"), "utf-16")


def test_read_text_unknown_declaration_falls_back_to_detection():
    assert "中文正文" in read_text("<p>中文正文</p>".encode("gbk"), "x-not-an-encoding")


def test_gb2312_mhtml_keeps_chinese(tmp_path):
    html = html_page('<meta charset="gb2312">').encode("gb2312")
    archive = (
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/related; boundary="b"\r\n\r\n'
        b"--b\r\n"
        b'Content-Type: text/html; charset="gb2312"\r\n'
        b"Content-Transfer-Encoding: base64\r\n\r\n" + base64.encodebytes(html) + b"\r\n--b--\r\n"
    )
    path = tmp_path / "page.mhtml"
    path.write_bytes(archive)
    text = all_text(DoclingEngine().parse(context(path, "html", "mhtml")))
    assert BODY in text
    assert "�" not in text


def test_mhtml_without_charset_header_keeps_chinese(tmp_path):
    """MIME 头没有 charset：不再交给 email 按 ASCII 解（X17 复审）。"""
    path = tmp_path / "page.mhtml"
    path.write_bytes(mhtml_archive(html_page("").encode("gbk"), "text/html"))
    text = all_text(DoclingEngine().parse(context(path, "html", "mhtml")))
    assert BODY in text
    assert HEADING in text
    assert "\ufffd" not in text


def test_mhtml_gb2312_declaration_with_gbk_only_char(tmp_path):
    """声明 gb2312、正文含 GBK 才有的字：按超集 GB18030 解（X17 复审）。"""
    html = f"<html><body><h1>{HEADING}</h1><p>{BODY}堃</p></body></html>".encode("gbk")
    path = tmp_path / "page.mhtml"
    path.write_bytes(mhtml_archive(html, 'text/html; charset="gb2312"'))
    text = all_text(DoclingEngine().parse(context(path, "html", "mhtml")))
    assert BODY in text
    assert "堃" in text
    assert "\ufffd" not in text


def test_gbk_markdown_keeps_chinese(tmp_path):
    path = tmp_path / "doc.md"
    path.write_bytes(f"# {HEADING}\n\n{BODY}\n".encode("gbk"))
    for engine in (DoclingEngine(), BuiltinEngine()):
        output = engine.parse(context(path, "markdown", "md"))
        headings = [item.text for item in output.document.texts if item.label == "section_header"]
        assert headings == [HEADING]
        assert BODY in all_text(output)


@pytest.mark.parametrize("control", ["\x0b", "\x1a", "\x1b[31m"])
def test_text_check_accepts_common_control_chars(tmp_path, control):
    path = tmp_path / "note.txt"
    path.write_bytes(f"第一行{control}第二行\r\n第三行\r\n".encode("gbk"))
    check_file(path, "txt")


def test_text_check_rejects_nul(tmp_path):
    path = tmp_path / "note.txt"
    path.write_bytes("中文\x00内容".encode())
    with pytest.raises(ParseError) as error:
        check_file(path, "txt")
    assert error.value.code == "FILE_CORRUPTED"


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (ConversionError("Input document bad.html is not valid."), "FILE_CORRUPTED"),
        (
            ConversionError("No pipeline could be initialized for format InputFormat.HTML"),
            "ENGINE_ERROR",
        ),
        (_BAD_ZIP_PIPELINE_ERROR, "FILE_CORRUPTED"),
        (RuntimeError("model files missing"), "ENGINE_ERROR"),
    ],
)
def test_docling_classifies_failures(tmp_path, monkeypatch, raised, code):
    path = tmp_path / "page.html"
    path.write_text(html_page(""), encoding="utf-8")

    class Broken:
        def convert(self, *_args, **_kwargs):
            raise raised

    engine = DoclingEngine()
    monkeypatch.setattr(engine, "_get_converter", lambda _mode: Broken())
    with pytest.raises(ParseError) as error:
        engine.parse(context(path, "html", "html"))
    assert error.value.code == code
    # 两种情况都用错误码表里的固定中文说明，不带 Docling 的英文报错和临时文件名（X17 复审）
    assert error.value.message == message_for(code)
    assert "Docling" not in error.value.message


def test_corrupted_pptx_is_file_corrupted(tmp_path):
    path = tmp_path / "bad.pptx"
    path.write_bytes(b"PK\x03\x04garbage")
    with pytest.raises(ParseError) as error:
        DoclingEngine().parse(context(path, "ppt", "pptx"))
    assert error.value.code == "FILE_CORRUPTED"


def test_pptx_page_count_does_not_map_every_failure_to_file_corrupted(tmp_path, monkeypatch):
    """读 pptx 页数只把“包读不出来”当文件损坏，其余（如内存不足）交给外层报 ENGINE_ERROR。"""
    import pptx

    path = tmp_path / "bad.pptx"
    path.write_bytes(b"PK\x03\x04garbage")

    def boom(*_args, **_kwargs):
        raise MemoryError("out of memory")

    monkeypatch.setattr(pptx, "Presentation", boom)
    # 引擎原样抛出，子进程的兜底会把非 ParseError 记成 ENGINE_ERROR（pool._worker）
    with pytest.raises(MemoryError):
        DoclingEngine().parse(context(path, "ppt", "pptx"))


def test_truncated_gif_is_file_corrupted(tmp_path):
    path = tmp_path / "broken.gif"
    path.write_bytes(b"GIF89a" + bytes(16))
    with pytest.raises(ParseError) as error:
        DoclingEngine().parse(context(path, "image", "gif"))
    assert error.value.code == "FILE_CORRUPTED"


def test_oversized_image_is_file_corrupted(tmp_path, monkeypatch):
    path = tmp_path / "huge.gif"
    Image.new("RGB", (100, 100)).save(path)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
    with pytest.raises(ParseError) as error:
        DoclingEngine().parse(context(path, "image", "gif"))
    assert error.value.code == "FILE_CORRUPTED"


def test_invalid_scan_mode_is_invalid_params(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(html_page(""), encoding="utf-8")
    with pytest.raises(ParseError) as error:
        DoclingEngine().parse(context(path, "html", "html", scan_mode="fast"))
    assert error.value.code == "INVALID_PARAMS"
    assert error.value.message == "scan_mode 必须为 auto 或 force"


def test_invalid_mineru_tier_is_invalid_params(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(ParseError) as error:
        MineruEngine().parse(context(path, "pdf", "pdf", tier="fast"))
    assert error.value.code == "INVALID_PARAMS"
    assert error.value.message == "不支持的 MinerU 解析档位：fast"


def test_invalid_params_has_message():
    assert message_for("INVALID_PARAMS") == "解析参数不正确"


def test_text_check_accepts_many_control_chars_without_nul(tmp_path):
    """控制字符多但没有 NUL：按“只看 NUL”的决定 B 通过（2026-10-28 用户决定）。"""
    path = tmp_path / "note.txt"
    path.write_bytes(bytes(range(1, 32)) * 4 + "少量中文".encode())
    check_file(path, "txt")
