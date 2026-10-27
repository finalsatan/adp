"""Docling 引擎回归测试（X06）。

样例全部现场生成（见 ``docling_samples.py``），不往仓库提交二进制。
扫描 PDF 的 OCR、webp 的转换依赖镜像内预置的 Docling 模型，所以快照测试在
``make parser-test-docker``（断网容器）里跑才完整；本机跑需要能访问模型站点。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.engines.base import ParseContext
from app.engines.docling_engine import DoclingEngine
from app.engines.mhtml import extract_mhtml
from tests.engines import docling_samples as samples


def context(path: Path, kind: str, ext: str, work: Path, report=None, **params) -> ParseContext:
    ctx = ParseContext(
        input_path=str(path),
        file_type=kind,
        ext=ext,
        engine_params=params,
        preprocess={},
        table={},
        work_dir=str(work),
        report=report or (lambda _done, _total: None),
    )
    return ctx


def test_pdf_scan_mode_reports_ocr_pages(tmp_path):
    engine = DoclingEngine()
    text_pdf = samples.write_text_pdf(tmp_path / "text.pdf")
    scan_pdf = samples.write_scan_pdf(tmp_path / "scan.pdf")
    ordinary = engine.parse(context(text_pdf, "pdf", "pdf", tmp_path))
    assert ordinary.pages == samples.TEXT_PDF_PAGES
    assert ordinary.ocr_pages == 0
    forced = engine.parse(context(text_pdf, "pdf", "pdf", tmp_path, scan_mode="force"))
    assert forced.ocr_pages == forced.pages == samples.TEXT_PDF_PAGES
    scanned = engine.parse(context(scan_pdf, "pdf", "pdf", tmp_path))
    assert scanned.pages == 1
    assert scanned.ocr_pages == 1
    assert (
        samples.SCAN_PDF_WORD.split()[0]
        in " ".join(item.text for item in scanned.document.texts).upper()
    )


def test_pdf_reports_page_level_progress(tmp_path):
    """Docling 的页级回调（2.142.1 的 progress_callback）要接到 X01 的进度上报上。"""
    path = samples.write_text_pdf(tmp_path / "text.pdf")
    reports: list[tuple[int, int]] = []
    DoclingEngine().parse(
        context(
            path, "pdf", "pdf", tmp_path, report=lambda done, total: reports.append((done, total))
        )
    )
    assert reports[0] == (0, samples.TEXT_PDF_PAGES)
    assert reports[-1] == (samples.TEXT_PDF_PAGES, samples.TEXT_PDF_PAGES)
    completed = [done for done, total in reports if total == samples.TEXT_PDF_PAGES]
    assert completed == sorted(completed)
    assert set(completed) == {0, 1, 2}


def test_warmup_loads_models_once_per_engine(tmp_path):
    """子进程报到 ready 前预热模型；预热后按扫描页判断复用同一个转换器。"""
    engine = DoclingEngine()
    engine.warmup()
    assert list(engine._converters) == ["auto"]
    converter = engine._converters["auto"]
    path = samples.write_text_pdf(tmp_path / "text.pdf")
    engine.parse(context(path, "pdf", "pdf", tmp_path))
    engine.parse(context(path, "pdf", "pdf", tmp_path, scan_mode="force"))
    assert engine._converters["auto"] is converter
    assert set(engine._converters) == {"auto", "force"}


def test_docx_preserves_heading_levels_without_pages(tmp_path):
    path = samples.write_headings_docx(tmp_path / "headings.docx")
    out = DoclingEngine().parse(context(path, "word", "docx", tmp_path))
    headings = [item for item in out.document.texts if item.label.value == "section_header"]
    assert [item.text for item in headings] == samples.DOCX_HEADINGS
    assert [item.level for item in headings] == [1, 2]
    # Word 没有页码（需求 4.1.8）
    assert all(not item.prov for item in headings)
    assert out.pages == 0
    out.document.validate_document()


def test_pptx_has_page_numbers_and_table(tmp_path):
    path = samples.write_table_pptx(tmp_path / "slides.pptx")
    out = DoclingEngine().parse(context(path, "ppt", "pptx", tmp_path))
    assert out.pages == len(samples.PPTX_PAGE_TITLES)
    assert len(out.document.pages) == len(samples.PPTX_PAGE_TITLES)
    assert len(out.document.tables) == 1
    assert sorted(page.page_no for page in out.document.pages.values()) == [1, 2]
    out.document.validate_document()


def test_webp_image_is_converted_and_recognized(tmp_path):
    path = samples.write_webp(tmp_path / "text.webp")
    out = DoclingEngine().parse(context(path, "image", "webp", tmp_path))
    assert out.pages == 1
    assert out.ocr_pages == 1
    assert (
        samples.WEBP_WORD.split()[0] in " ".join(item.text for item in out.document.texts).upper()
    )


def test_mhtml_extracts_body_and_embedded_image(tmp_path):
    archive = samples.write_mhtml(tmp_path / "sample.mhtml")
    html = extract_mhtml(str(archive), str(tmp_path / "extracted.html"))
    content = html.read_text()
    assert samples.MHTML_TITLE in content
    assert "data:image/png;base64,aGVsbG8=" in content


def test_html_never_fetches_remote_images(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = samples.write_remote_image_html(tmp_path / "remote.html", server.server_port)
        out = DoclingEngine().parse(context(path, "html", "html", tmp_path))
        assert any(samples.HTML_TITLE in item.text for item in out.document.texts)
        assert requests == []
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
