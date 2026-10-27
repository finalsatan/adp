"""生成 X08 快照测试用的样例文档（只在重新生成样例时运行）。

生成 ``sample.pdf``、``sample.docx``、``sample.pptx``：都带标题、段落、合并单元格
表格和图片，PDF 另外带公式和多页坐标。生成后用 ``refresh.py`` 把它们发给本地
mineru 服务，把 middle_json 和图片存成快照样例。

需要 python-docx、python-pptx、reportlab、pillow：

    uv run --with python-docx --with python-pptx python tests/engines/mineru/samples/make_samples.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from docx import Document
from docx.shared import Inches
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches as PInches
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
# 样例图只是中间产物，不进样例目录
FIGURES = Path(tempfile.mkdtemp(prefix="mineru-sample-figures-"))


def _figure(path: Path) -> Path:
    """几个色块的图：版面模型会判成图片（画成表格样式会被判成表格）。"""
    image = Image.new("RGB", (240, 150), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, 110, 70], fill=(200, 60, 60))
    draw.rectangle([130, 10, 230, 70], fill=(60, 120, 200))
    draw.ellipse([40, 80, 200, 140], fill=(240, 200, 80))
    image.save(path, optimize=True)
    return path


def make_pdf(path: Path) -> None:
    figure = _figure(FIGURES / "pdf-figure.png")
    pdf = canvas.Canvas(str(path), pagesize=A4, pageCompression=1)
    width, height = A4

    # 第 1 页：标题、段落、带合并单元格的表格、图片、公式
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(72, height - 90, "MinerU Sample Report")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, height - 130, "This is the first paragraph of the sample PDF.")
    pdf.drawString(72, height - 146, "It has a second line that belongs to the same paragraph.")

    x0, y0, row_h = 72, height - 260, 24
    col_w = [120, 90, 90]
    for row in range(4):
        pdf.line(x0, y0 + row * row_h, x0 + sum(col_w), y0 + row * row_h)
        # 最后一行前两列合并：不画中间那条竖线
        xs = (
            [x0, x0 + sum(col_w)]
            if row == 3
            else [x0, x0 + col_w[0], x0 + col_w[0] + col_w[1], x0 + sum(col_w)]
        )
        for x in xs:
            pdf.line(x, y0 + row * row_h, x, y0 + (row + 1) * row_h)
    pdf.setFont("Helvetica", 10)
    for row, cells in (
        (0, ["Region", "Q1", "Q2"]),
        (1, ["North", "12", "15"]),
        (2, ["South", "9", "11"]),
    ):
        y = y0 + (3 - row) * row_h + 8
        pdf.drawString(x0 + 6, y, cells[0])
        pdf.drawString(x0 + col_w[0] + 6, y, cells[1])
        pdf.drawString(x0 + col_w[0] + col_w[1] + 6, y, cells[2])
    pdf.drawString(x0 + 6, y0 + 8, "Total")
    pdf.drawString(x0 + col_w[0] + col_w[1] + 6, y0 + 8, "23")

    pdf.drawImage(ImageReader(str(figure)), 72, height - 420, width=200, height=125)
    pdf.setFont("Times-Italic", 20)
    pdf.drawCentredString(width / 2, height - 480, "f(x) = 1 / (1 + e^-x)")

    # 第 2 页：二级标题和正文，用来核对第二页的坐标
    pdf.showPage()
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(72, height - 90, "Section Two")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, height - 120, "Text on the second page for coordinate checks.")
    pdf.showPage()
    pdf.save()


def make_docx(path: Path) -> None:
    figure = _figure(FIGURES / "docx-figure.png")
    document = Document()
    document.add_heading("季度经营分析报告", level=1)
    document.add_paragraph("本报告汇总了本季度的经营数据，供管理层决策参考。")
    document.add_heading("一、收入情况", level=2)
    document.add_paragraph("收入同比增长百分之十二，主要来自华东区。")
    document.add_heading("1.1 分区域明细", level=3)

    table = document.add_table(rows=3, cols=3)
    for index, text in enumerate(["区域", "收入", "同比"]):
        table.cell(0, index).text = text
    table.cell(1, 0).text = "华东"
    table.cell(1, 1).text = "1200"
    table.cell(1, 2).text = "12%"
    table.cell(2, 0).merge(table.cell(2, 1))
    table.cell(2, 0).text = "合计"
    table.cell(2, 2).text = "1200"

    document.add_paragraph("下图是收入趋势：")
    document.add_picture(str(figure), width=Inches(3))
    document.save(str(path))


def make_pptx(path: Path) -> None:
    figure = _figure(FIGURES / "pptx-figure.png")
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "智能体开发平台介绍"
    slide.placeholders[1].text = "解析服务与知识库"

    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "一、解析引擎"
    body = slide.placeholders[1].text_frame
    body.text = "Docling：PDF 与 Office"
    body.add_paragraph().text = "MinerU：版面与公式还原"
    body.add_paragraph().text = "内置解析：Excel 与文本"
    slide.shapes.add_picture(str(figure), PInches(6), PInches(1.5), width=PInches(3))
    presentation.save(str(path))


if __name__ == "__main__":
    make_pdf(HERE / "sample.pdf")
    make_docx(HERE / "sample.docx")
    make_pptx(HERE / "sample.pptx")
    for name in ("sample.pdf", "sample.docx", "sample.pptx"):
        item = HERE / name
        print(item.name, item.stat().st_size)
