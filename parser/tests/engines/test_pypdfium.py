"""pypdfium PDF 引擎的测试（X05）。

样例 PDF 全部现场生成（见 ``pdf_samples.py``），不往仓库提交二进制。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from docling_core.types.doc.labels import DocItemLabel

from app.engines.base import ParseContext
from app.engines.pypdfium_engine import PypdfiumEngine
from app.engines.registry import bootstrap, get_engine
from app.errors import ParseError
from app.postprocess import preprocess
from tests.engines import pdf_samples

ENGINE = PypdfiumEngine()

# A4 页面尺寸，用于检查坐标是否落在页面内
A4_WIDTH, A4_HEIGHT = pdf_samples.PAGE_SIZE


def make_ctx(path: Path, reports: list | None = None) -> ParseContext:
    return ParseContext(
        input_path=str(path),
        file_type="pdf",
        ext="pdf",
        engine_params={},
        preprocess={},
        table={},
        work_dir=str(path.parent),
        report=lambda done, total: reports.append((done, total)) if reports is not None else None,
    )


def body_texts(document) -> list:
    return [item for item in document.texts if item.label == DocItemLabel.TEXT]


# ---- 多页文档 ----


def test_multipage_snapshot(tmp_path):
    """段落数、页数正确，每段的页码正确，坐标落在页面范围内。"""
    path = pdf_samples.write_multipage_pdf(tmp_path / "multi.pdf")
    output = ENGINE.parse(make_ctx(path))

    assert output.pages == 3
    assert len(output.document.pages) == 3
    assert output.engine_version.startswith("pypdfium")
    assert output.ocr_pages == 0
    assert output.empty_pages == []

    body = body_texts(output.document)
    # 第 1、3 页各两段，第 2 页一段
    assert len(body) == 5
    assert [item.prov[0].page_no for item in body] == [1, 1, 2, 3, 3]
    assert [item.text for item in body] == [
        "First paragraph line one, first paragraph line two.",
        "Second paragraph on page one.",
        "Only paragraph on page two.",
        "Third page first paragraph.",
        "Third page second paragraph.",
    ]

    # 每个元素（含页眉页脚）的坐标都落在页面范围内
    for item in output.document.texts:
        prov = item.prov[0]
        bbox = prov.bbox
        assert 1 <= prov.page_no <= output.pages
        assert 0 <= bbox.l <= bbox.r <= A4_WIDTH + 1
        assert 0 <= bbox.b <= bbox.t <= A4_HEIGHT + 1


def test_header_footer_detected(tmp_path):
    """重复的页眉、只差数字的页脚被标出。"""
    path = pdf_samples.write_multipage_pdf(tmp_path / "multi.pdf")
    output = ENGINE.parse(make_ctx(path))

    headers = [item for item in output.document.texts if item.label == DocItemLabel.PAGE_HEADER]
    footers = [item for item in output.document.texts if item.label == DocItemLabel.PAGE_FOOTER]

    assert [item.text for item in headers] == [pdf_samples.MULTIPAGE_HEADER] * 3
    assert [item.prov[0].page_no for item in headers] == [1, 2, 3]
    # 页脚只差数字，归为同一行
    assert [item.text for item in footers] == ["Page 1", "Page 2", "Page 3"]


def test_header_footer_removed_by_preprocess(tmp_path):
    """开启预处理后页眉页脚被删除，其他元素的 prov 不变。"""
    path = pdf_samples.write_multipage_pdf(tmp_path / "multi.pdf")
    output = ENGINE.parse(make_ctx(path))
    before = [
        (item.text, item.prov[0].model_copy(deep=True)) for item in body_texts(output.document)
    ]

    preprocess(output.document, {"remove_header_footer": True})

    labels = {item.label for item in output.document.texts}
    assert DocItemLabel.PAGE_HEADER not in labels
    assert DocItemLabel.PAGE_FOOTER not in labels
    after = [(item.text, item.prov[0]) for item in body_texts(output.document)]
    assert len(after) == 5

    # 正文文字和 prov 不变（删除页眉页脚会重编号 self_ref，所以按文字对应）
    assert [text for text, _ in before] == [text for text, _ in after]
    for (_, prov_before), (_, prov_after) in zip(before, after, strict=True):
        assert prov_after.page_no == prov_before.page_no
        assert prov_after.bbox == prov_before.bbox


def test_progress_reported_per_page(tmp_path):
    path = pdf_samples.write_multipage_pdf(tmp_path / "multi.pdf")
    reports: list[tuple[int, int]] = []
    ENGINE.parse(make_ctx(path, reports))

    assert reports == [(1, 3), (2, 3), (3, 3)]


# ---- 隐藏文字 ----


def test_hidden_text_filtered(tmp_path):
    """渲染模式不可见的文字不进结果（防提示词注入）。"""
    path = pdf_samples.write_hidden_text_pdf(tmp_path / "hidden.pdf")
    output = ENGINE.parse(make_ctx(path))

    texts = [item.text for item in output.document.texts]
    assert texts == [pdf_samples.HIDDEN_VISIBLE]
    assert all(pdf_samples.HIDDEN_INJECTION not in text for text in texts)
    # 隐藏文字被过滤后这一页仍有可见文字，不算空页
    assert output.empty_pages == []


# ---- 扫描页 ----


def test_scanned_page_in_empty_pages(tmp_path):
    """没有文字层的页解析为空，页码记入 empty_pages，不报错。"""
    path = pdf_samples.write_scanned_page_pdf(tmp_path / "scan.pdf")
    output = ENGINE.parse(make_ctx(path))

    assert output.pages == 3
    assert output.empty_pages == [pdf_samples.SCANNED_EMPTY_PAGE]
    assert [item.prov[0].page_no for item in output.document.texts] == [1, 3]
    # 空页仍记录在文档的页面列表里
    assert set(output.document.pages) == {1, 2, 3}


# ---- 错误 ----


def test_encrypted_pdf(tmp_path):
    path = pdf_samples.write_encrypted_pdf(tmp_path / "encrypted.pdf")
    with pytest.raises(ParseError) as exc:
        ENGINE.parse(make_ctx(path))
    assert exc.value.code == "FILE_ENCRYPTED"


def test_corrupted_pdf(tmp_path):
    path = pdf_samples.write_corrupted_pdf(tmp_path / "broken.pdf")
    with pytest.raises(ParseError) as exc:
        ENGINE.parse(make_ctx(path))
    assert exc.value.code == "FILE_CORRUPTED"


# ---- 引擎能力与注册 ----


def test_supports_and_availability():
    assert ENGINE.availability() == (True, None)
    assert ENGINE.supports("pdf", "pdf")
    assert not ENGINE.supports("pdf", "txt")
    assert not ENGINE.supports("txt", "txt")


def test_unsupported_type_rejected(tmp_path):
    path = pdf_samples.write_multipage_pdf(tmp_path / "multi.pdf")
    ctx = make_ctx(path)
    ctx.file_type = "txt"
    ctx.ext = "txt"
    with pytest.raises(ParseError) as exc:
        ENGINE.parse(ctx)
    assert exc.value.code == "UNSUPPORTED_TYPE"


def test_registry_registers_pypdfium():
    bootstrap(enable_fake_engine=False)
    engine = get_engine("pypdfium")
    assert engine is not None
    assert engine.name == "pypdfium"
    assert engine.supports("pdf", "pdf")
