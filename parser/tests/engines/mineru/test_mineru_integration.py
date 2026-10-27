"""连本地 mineru 容器的集成测试，默认不跑（需要 ``make parser-dev-up``）。

    MINERU_URL=http://127.0.0.1:59003 uv run pytest -m integration tests/engines/mineru

覆盖卡片验收标准里的三条：扫描 PDF 用基础档能解析出文字；docx、epub 各一份能解析。
"""

from __future__ import annotations

import os
import socket
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.engines.base import ParseContext
from app.engines.mineru.engine import MineruEngine
from tests.engines.mineru.sample_data import SAMPLES_DIR

pytestmark = pytest.mark.integration

DEFAULT_URL = "http://127.0.0.1:59003"


def _service_url() -> str:
    return os.environ.get("MINERU_URL") or DEFAULT_URL


def _reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def mineru_url() -> str:
    url = _service_url()
    if not _reachable(url):
        pytest.skip(f"mineru 未启动（{url}），先执行 make parser-dev-up")
    return url


@pytest.fixture
def engine(mineru_url, monkeypatch):
    monkeypatch.setenv("MINERU_URL", mineru_url)
    monkeypatch.setenv("MINERU_API_KEY", os.environ.get("MINERU_API_KEY", "adpdev"))
    return MineruEngine(poll_interval=0.5)


def context(path: Path, file_type: str, ext: str, params: dict | None = None) -> ParseContext:
    return ParseContext(
        input_path=str(path),
        file_type=file_type,
        ext=ext,
        engine_params=params or {},
        preprocess={},
        table={},
        work_dir=str(path.parent),
        report=lambda done, total: None,
    )


def test_scanned_pdf_with_basic_tier_ocr(tmp_path, engine):
    """只有图片、没有文字层的 PDF，基础档要 OCR 出文字。"""
    image = Image.new("RGB", (900, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 100), "Scanned Invoice 2026", fill="black", font=ImageFont.load_default(size=56))
    pdf_path = tmp_path / "scanned.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=A4, pageCompression=1)
    pdf.drawImage(ImageReader(image), 72, 400, width=450, height=130)
    pdf.showPage()
    pdf.save()

    output = engine.parse(context(pdf_path, "pdf", "pdf", {"tier": "basic"}))

    assert output.pages == 1
    assert output.ocr_pages == 1
    text = " ".join(item.text for item in output.document.texts)
    assert "Scanned Invoice 2026" in text
    # PDF 的块带坐标，换算到页面尺寸之内
    page = output.document.pages[1]
    bbox = output.document.texts[0].prov[0].bbox
    assert 0 <= bbox.l < bbox.r <= page.size.width
    assert 0 <= bbox.b < bbox.t <= page.size.height


def test_docx_with_headings_and_table(engine):
    """docx 由 MinerU 直接读取，不论传什么档位都用快速档。"""
    source = SAMPLES_DIR / "sample.docx"
    output = engine.parse(context(source, "word", "docx", {"tier": "standard"}))

    levels = {item.text: getattr(item, "level", None) for item in output.document.texts}
    assert levels["季度经营分析报告"] == 1
    assert levels["一、收入情况"] == 2
    assert len(output.document.tables) == 1
    table = output.document.tables[0]
    assert table.data.num_rows == 3
    merged = [cell for cell in table.data.table_cells if cell.col_span > 1]
    assert merged and merged[0].text == "合计"


def test_epub(tmp_path, engine):
    source = tmp_path / "sample.epub"
    _write_epub(source)
    output = engine.parse(context(source, "epub", "epub"))

    texts = [item.text for item in output.document.texts]
    assert "第一章 解析引擎" in texts
    assert any("这是 EPUB 样例的第一段正文。" in text for text in texts)
    assert output.pages >= 1


def _write_epub(path: Path) -> None:
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bookid">urn:uuid:adp-x08-sample</dc:identifier>'
        "<dc:title>解析服务样例电子书</dc:title><dc:language>zh-CN</dc:language></metadata>"
        '<manifest><item id="chapter1" href="chapter1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="chapter1"/></spine></package>'
    )
    chapter = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第一章</title></head><body>'
        "<h1>第一章 解析引擎</h1><p>这是 EPUB 样例的第一段正文。</p>"
        "<h2>1.1 解析档位</h2><p>快速档读取文字层，基础档做 OCR。</p></body></html>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        archive.writestr(info, "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter1.xhtml", chapter)


def test_health_endpoint(mineru_url):
    import httpx

    response = httpx.get(f"{mineru_url}/v1/health", timeout=5)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"].startswith("4.")
    # 快速、基础两档在同一个服务里，ZIP 产物可用
    assert "zip" in payload["features"]["output_formats"]
