"""非 UTF-8 文本的解析、文本检查和错误码（X17）。"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from docling.exceptions import ConversionError

from app.engines.base import ParseContext, ParseOutput
from app.engines.builtin import BuiltinEngine
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
    if code == "ENGINE_ERROR":
        # 引擎故障不把异常细节说给用户
        assert error.value.message == message_for("ENGINE_ERROR")


def test_corrupted_pptx_is_file_corrupted(tmp_path):
    path = tmp_path / "bad.pptx"
    path.write_bytes(b"PK\x03\x04garbage")
    with pytest.raises(ParseError) as error:
        DoclingEngine().parse(context(path, "ppt", "pptx"))
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


def test_text_check_rejects_mostly_control_chars(tmp_path):
    path = tmp_path / "note.txt"
    path.write_bytes(bytes(range(1, 32)) * 4 + "少量中文".encode())
    with pytest.raises(ParseError) as error:
        check_file(path, "txt")
    assert error.value.code == "FILE_CORRUPTED"
