"""统一后处理的测试：预处理、图片、导出（X03）。"""

from __future__ import annotations

import warnings
from pathlib import Path

from docling_core.types.doc.common.content_layer import ContentLayer
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel
from PIL import Image as PILImage

from app import doctools, postprocess
from app.postprocess import PostprocessResult
from app.storage import Storage
from tests.conftest import make_settings

PAGE = (1, 595, 842)


def _new_doc() -> DoclingDocument:
    document = doctools.new_document("test")
    doctools.add_page(document, *PAGE)
    return document


def _provs(document: DoclingDocument):
    return [
        (item.prov[0].page_no, item.prov[0].bbox.l, item.prov[0].bbox.t) for item in document.texts
    ]


def _storage() -> Storage:
    return Storage(make_settings())


def _run(output, tmp_path, options=None) -> PostprocessResult:
    return postprocess.postprocess(
        output,
        options=options or {},
        work_dir=tmp_path,
        images_prefix="out/parsed/original/images",
        storage=_storage(),
    )


# ---- 预处理 ----


def test_merge_whitespace():
    document = _new_doc()
    doctools.add_paragraph(document, "  第一段   文字\n换行  ", page_no=1, bbox=(10, 20, 100, 10))
    doctools.add_paragraph(document, "hello   world", page_no=1, bbox=(10, 30, 100, 20))
    doctools.add_paragraph(document, "中文\n之间", page_no=1, bbox=(10, 40, 100, 30))

    postprocess.merge_whitespace(document)

    # 中文之间的空白直接去掉，英文之间的连续空白合并成一个
    assert [item.text for item in document.texts] == ["第一段文字换行", "hello world", "中文之间"]


def test_merge_whitespace_off_keeps_text():
    document = _new_doc()
    doctools.add_paragraph(document, "a   b", page_no=1, bbox=(10, 20, 100, 10))

    postprocess.preprocess(document, {"merge_whitespace": False})

    assert document.texts[0].text == "a   b"


def test_remove_header_footer_keeps_other_prov():
    document = _new_doc()
    doctools.add_paragraph(document, "正文", page_no=1, bbox=(10, 20, 100, 10))
    # 挂在 furniture 组下的页眉
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        furniture = document.furniture
    header = document.add_text(
        label=DocItemLabel.PAGE_HEADER,
        text="页眉",
        prov=doctools.provenance(1, (10, 800, 100, 780)),
        parent=furniture,
        content_layer=ContentLayer.FURNITURE,
    )
    assert header is not None
    # body 标签为 page_footer 的页脚
    doctools.add_paragraph(
        document, "页脚", label=DocItemLabel.PAGE_FOOTER, page_no=1, bbox=(10, 40, 100, 30)
    )
    doctools.add_paragraph(document, "正文二", page_no=1, bbox=(10, 60, 100, 50))
    before = _provs(document)

    postprocess.remove_header_footer(document)

    assert [item.text for item in document.texts] == ["正文", "正文二"]
    assert _provs(document) == [before[0], before[-1]]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert document.furniture.children == []


def test_remove_header_footer_off_keeps_items():
    document = _new_doc()
    doctools.add_paragraph(document, "正文", page_no=1, bbox=(10, 20, 100, 10))
    doctools.add_paragraph(
        document, "页眉", label=DocItemLabel.PAGE_HEADER, page_no=1, bbox=(10, 800, 100, 780)
    )

    postprocess.preprocess(document, {"remove_header_footer": False})

    assert [item.text for item in document.texts] == ["正文", "页眉"]


def test_remove_links_emails():
    document = _new_doc()
    doctools.add_paragraph(
        document,
        "见 https://example.com/a 或 www.example.com，联系 a.b+c@example.com。",
        page_no=1,
        bbox=(10, 20, 100, 10),
    )

    postprocess.remove_links_emails(document)

    text = document.texts[0].text
    assert "https://" not in text
    assert "www." not in text
    assert "@" not in text
    assert "见" in text and "联系" in text


def test_remove_links_emails_off_keeps_text():
    document = _new_doc()
    doctools.add_paragraph(document, "https://example.com", page_no=1, bbox=(10, 20, 100, 10))

    postprocess.preprocess(document, {"remove_links_emails": False})

    assert document.texts[0].text == "https://example.com"


# ---- 图片 ----


def test_images_are_uploaded_and_referenced(s3, tmp_path):
    document = _new_doc()
    doctools.add_paragraph(document, "正文", page_no=1, bbox=(10, 20, 100, 10))
    doctools.add_picture(
        document,
        PILImage.new("RGB", (12, 8), "white"),
        caption="图里的文字",
        page_no=1,
        bbox=(10, 200, 110, 100),
    )

    result = _run(_output(document), tmp_path)

    assert result.images == 1
    assert document.pictures[0].image.uri == Path("images/1.png")
    uploaded = s3.get_object(Bucket="test-bucket", Key="out/parsed/original/images/1.png")[
        "Body"
    ].read()
    assert uploaded.startswith(b"\x89PNG")

    raw = (tmp_path / "out" / "doc.json").read_text(encoding="utf-8")
    assert "base64" not in raw
    assert "data:image" not in raw

    markdown = (tmp_path / "out" / "doc.md").read_text(encoding="utf-8")
    assert "![Image](images/1.png)" in markdown
    assert "图里的文字" in markdown


def test_picture_caption_from_text_child(s3, tmp_path):
    document = _new_doc()
    picture = document.add_picture(prov=doctools.provenance(1, (10, 200, 110, 100)))
    document.add_text(
        label=DocItemLabel.TEXT, text="图内文字", prov=doctools.provenance(1), parent=picture
    )

    _run(_output(document), tmp_path)

    assert [ref.resolve(document).text for ref in document.pictures[0].captions] == ["图内文字"]
    markdown = (tmp_path / "out" / "doc.md").read_text(encoding="utf-8")
    assert "图内文字" in markdown


# ---- 表格与导出 ----


def test_table_exported_as_markdown(s3, tmp_path):
    document = _new_doc()
    doctools.add_table(
        document,
        [["姓名", "部门"], ["张三", "研发"], ["李四", "运营"]],
        header_rows=1,
        page_no=1,
        bbox=(10, 400, 200, 300),
    )

    _run(_output(document), tmp_path)

    markdown = (tmp_path / "out" / "doc.md").read_text(encoding="utf-8")
    assert "| 姓名" in markdown
    assert "| 张三" in markdown
    assert "| ---" in markdown or "|---" in markdown


def test_merged_cells_survive_export(s3, tmp_path):
    document = _new_doc()
    doctools.add_table(
        document,
        [["合并表头", ""], ["a", "b"]],
        header_rows=1,
        merges=[(0, 0, 1, 2)],
        page_no=1,
        bbox=(10, 400, 200, 300),
    )
    assert document.tables[0].data.num_cols == 2
    spans = {
        (c.start_row_offset_idx, c.start_col_offset_idx): (c.row_span, c.col_span)
        for c in document.tables[0].data.table_cells
    }
    assert spans[(0, 0)] == (1, 2)

    _run(_output(document), tmp_path)

    reloaded = DoclingDocument.load_from_json(tmp_path / "out" / "doc.json")
    assert reloaded.tables[0].data.num_cols == 2


def test_exported_json_reloads_with_same_structure(s3, tmp_path):
    document = _new_doc()
    doctools.add_heading(document, "标题", level=1, page_no=1, bbox=(10, 800, 100, 780))
    doctools.add_paragraph(document, "正文", page_no=1, bbox=(10, 700, 100, 680))
    doctools.add_table(document, [["h"], ["v"]], page_no=1, bbox=(10, 600, 100, 500))
    doctools.add_picture(
        document, PILImage.new("RGB", (4, 4), "black"), page_no=1, bbox=(10, 400, 50, 350)
    )
    before = _provs(document)

    _run(_output(document), tmp_path)
    reloaded = DoclingDocument.load_from_json(tmp_path / "out" / "doc.json")

    assert len(reloaded.texts) == len(document.texts)
    assert len(reloaded.tables) == len(document.tables)
    assert len(reloaded.pictures) == len(document.pictures)
    assert set(reloaded.pages) == set(document.pages)
    assert _provs(reloaded) == before


def test_runner_uploads_docs_and_images(s3, tmp_path):
    from app.jobs import Job, JobStore, TaskSpec
    from app.runner import Runner

    document = _new_doc()
    doctools.add_picture(
        document, PILImage.new("RGB", (4, 4), "white"), page_no=1, bbox=(10, 200, 50, 100)
    )
    job = Job(
        id="job",
        status="running",
        task=TaskSpec(
            source_key="in/a.txt",
            file_type="txt",
            ext="txt",
            engine="fake",
            engine_params={},
            preprocess={},
            table={},
            output_prefix="out/parsed/original",
            images_prefix="out/parsed/original/images",
        ),
        created_at=0,
        started_at=0,
    )
    job.work_dir = str(tmp_path)
    runner = Runner(make_settings(), JobStore(10), _storage())

    result = runner.finish(job, _output(document))

    assert result["images"] == 1
    assert result["doc_key"] == "out/parsed/original/doc.json"
    s3.get_object(Bucket="test-bucket", Key="out/parsed/original/images/1.png")
    doc_key = "out/parsed/original/doc.json"
    doc_bytes = s3.get_object(Bucket="test-bucket", Key=doc_key)["Body"].read()
    assert b"base64" not in doc_bytes
    md_bytes = s3.get_object(Bucket="test-bucket", Key="out/parsed/original/doc.md")["Body"].read()
    assert b"![Image](images/1.png)" in md_bytes


def _output(document: DoclingDocument):
    from app.engines.base import ParseOutput

    return ParseOutput(document=document, pages=1, ocr_pages=0, engine_version="test")
