"""Docling 引擎回归测试（X06）。

样例全部现场生成（见 ``docling_samples.py``），不往仓库提交二进制。
扫描 PDF 的 OCR、webp 的转换依赖镜像内预置的 Docling 模型，所以快照测试在
``make parser-test-docker``（断网容器）里跑才完整；本机跑需要能访问模型站点。
"""

from __future__ import annotations

import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app import doctools
from app.engines.base import ParseContext
from app.engines.docling_engine import DoclingEngine, _add_pptx_tables
from app.engines.mhtml import extract_mhtml
from tests.conftest import submit
from tests.engines import docling_samples as samples
from tests.test_api import poll

PARSER_ROOT = Path(__file__).resolve().parents[2]


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


def test_pptx_keeps_every_native_table_once(tmp_path):
    """三个原生表格全部保留，且不重复（X18）。"""
    path = samples.write_three_table_pptx(tmp_path / "three.pptx")
    out = DoclingEngine().parse(context(path, "ppt", "pptx", tmp_path))
    assert len(out.document.tables) == samples.PPTX_TABLE_PAGES
    assert sorted(table.prov[0].page_no for table in out.document.tables) == [1, 2, 3]
    out.document.validate_document()


def test_add_pptx_tables_only_fills_pages_without_tables(tmp_path):
    """Docling 已经抽到某页的表格时，只补没抽到的页，不整份重加一遍（X18）。"""
    path = samples.write_three_table_pptx(tmp_path / "three.pptx")
    document = doctools.new_document("three")
    for page_no in range(1, samples.PPTX_TABLE_PAGES + 1):
        doctools.add_page(document, page_no, 600.0, 400.0)
    # 模拟 Docling 自己抽到了第 1 页的表格
    doctools.add_table(document, [["a", "b"], ["c", "d"]], page_no=1)

    _add_pptx_tables(path, document)

    assert len(document.tables) == samples.PPTX_TABLE_PAGES
    assert sorted(table.prov[0].page_no for table in document.tables) == [1, 2, 3]
    assert document.tables[0].data.num_rows == 2


def test_webp_image_is_converted_and_recognized(tmp_path):
    path = samples.write_webp(tmp_path / "text.webp")
    out = DoclingEngine().parse(context(path, "image", "webp", tmp_path))
    assert out.pages == 1
    assert out.ocr_pages == 1
    assert (
        samples.WEBP_WORD.split()[0] in " ".join(item.text for item in out.document.texts).upper()
    )


def test_pdf_ocr_pages_only_counts_pages_without_text_layer(tmp_path):
    """只有部分页没有文字层时，ocr_pages 等于实际 OCR 的页数，不能重复计数（X18）。"""
    path = samples.write_mixed_pdf(tmp_path / "mixed.pdf")
    out = DoclingEngine().parse(context(path, "pdf", "pdf", tmp_path))
    assert out.pages == samples.MIXED_PDF_PAGES
    assert out.ocr_pages == samples.MIXED_PDF_OCR_PAGES


def test_multiframe_tiff_counts_frames_as_pages(tmp_path):
    """多帧 TIFF 由 Docling 逐帧处理，页数和 OCR 页数按帧数算（X18）。"""
    path = samples.write_multiframe_tiff(tmp_path / "multi.tiff")
    out = DoclingEngine().parse(context(path, "image", "tiff", tmp_path))
    assert out.pages == samples.TIFF_FRAMES
    assert out.ocr_pages == samples.TIFF_FRAMES
    assert len(out.document.pages) == samples.TIFF_FRAMES


def test_multiframe_tiff_over_page_limit_fails_job(make_client, s3, tmp_path):
    """多帧 TIFF 超过 PARSER_MAX_PAGES 时返回 TOO_MANY_PAGES（X18）。"""
    path = samples.write_multiframe_tiff(tmp_path / "multi.tiff")
    client = make_client(parser_workers=1, parser_max_pages=samples.TIFF_FRAMES - 1)
    s3.put_object(Bucket="test-bucket", Key="in/multi.tiff", Body=path.read_bytes())

    response = submit(
        client, source_key="in/multi.tiff", file_type="image", ext="tiff", engine="docling"
    )
    assert response.status_code == 202, response.text
    status = poll(client, response.json()["id"], {"succeeded", "failed"}, timeout=180)
    assert status["status"] == "failed", status
    assert status["error"]["code"] == "TOO_MANY_PAGES"


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


def test_main_process_does_not_load_docling():
    """主进程（注册表 bootstrap + 应用模块）不加载 docling、torch（X18）。"""
    probe = (
        "import sys;"
        "import app.convert, app.main, app.pool, app.runner;"
        "from app.engines.registry import bootstrap;"
        "bootstrap(False);"
        "print(','.join(name for name in ('docling', 'torch') if name in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PARSER_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
