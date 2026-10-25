"""入库流水线步骤 1（转换）、步骤 2（解析）（P3-09，知识库技术方案 8.1、8.3）。

连真实 PostgreSQL、Redis、RustFS，解析服务用 `backend/tests/fake_parser` 的假服务。
"""

from __future__ import annotations

import asyncio
import json

import pytest
import sqlalchemy as sa

from app.core.storage import doc_file_key, images_prefix
from app.kb import pipeline
from app.kb.tables import Document, DocumentParse, ParseRecord
from app.worker.dispatch import RunCancelled


async def _run(app, parser_client, context) -> None:
    await pipeline.run_pipeline(
        app.state.sessionmaker,
        app.state.redis,
        context.doc_id,
        context.run_id,
        settings=app.state.settings,
        storage=app.state.storage,
        parser_client=parser_client,
    )


@pytest.fixture(autouse=True)
def _parse_steps_only(monkeypatch) -> None:
    """本文件只测步骤 1、2；步骤 3 起的流水线由 P3-11 接入，这里替换成空实现。"""

    async def _noop(*args, **kwargs) -> None:
        return None

    for name in ("_step_chunk", "_step_embed", "_step_activate"):
        monkeypatch.setattr(pipeline, name, _noop)


async def _document(app, doc_id: int) -> Document:
    async with app.state.sessionmaker() as session:
        doc = await session.get(Document, doc_id)
        assert doc is not None
        return doc


async def _parse_row(app, doc_id: int) -> DocumentParse | None:
    async with app.state.sessionmaker() as session:
        return await session.get(DocumentParse, (doc_id, "original"))


async def _records(app, doc_id: int) -> list[ParseRecord]:
    async with app.state.sessionmaker() as session:
        result = await session.execute(sa.select(ParseRecord).where(ParseRecord.doc_id == doc_id))
        return list(result.scalars().all())


async def test_parse_success(app, parser_client, make_ingest_document) -> None:
    """解析成功：写 document_parses / parse_records，文档页数与状态正确，产物在对象存储。"""
    context = await make_ingest_document()
    await _run(app, parser_client, context)

    doc = await _document(app, context.doc_id)
    assert doc.status == "chunking"
    assert doc.step == 3
    assert doc.page_count == 2
    assert doc.failed_step is None
    assert doc.error_code is None

    parsed = await _parse_row(app, context.doc_id)
    assert parsed is not None
    assert parsed.kind == "original"
    assert parsed.engine == "docling"
    assert parsed.engine_version == "docling-fake-1.0"
    assert parsed.pages == 2
    assert parsed.created_by == context.user_id
    assert parsed.doc_key.endswith("parsed/original/doc.json")
    assert parsed.md_key.endswith("parsed/original/doc.md")

    records = await _records(app, context.doc_id)
    assert len(records) == 1
    assert records[0].ok is True
    assert records[0].pages == 2
    assert records[0].error_code is None

    storage = app.state.storage
    doc_json_key = doc_file_key(context.kb_id, context.doc_id, "parsed/original/doc.json")
    assert await storage.exists(doc_json_key)
    md_json_key = doc_file_key(context.kb_id, context.doc_id, "parsed/original/doc.md")
    assert await storage.exists(md_json_key)
    assert await storage.exists(f"{images_prefix(context.kb_id, context.doc_id)}1.png")

    # PDF 样例含页码、坐标和表格（知识库技术方案 7）。
    payload = json.loads(await storage.get_bytes(doc_json_key))
    assert payload["texts"][0]["prov"][0]["page_no"] == 1
    assert payload["texts"][0]["prov"][0]["bbox"]["l"] == 72
    assert payload["tables"]
    assert payload["pages"]["1"]["size"]["width"] == 595


async def test_temporary_error_retries_then_succeeds(
    app, parser_client, fake_parser_app, make_ingest_document, monkeypatch
) -> None:
    """临时错误自动重试 3 次后成功：共提交 4 次，最终只有一条成功记录。"""
    monkeypatch.setattr(pipeline, "RETRY_BASE_SECONDS", 0.01)
    context = await make_ingest_document(
        parse_params={"error_code": "TIMEOUT", "temporary_fail_times": 3}
    )
    await _run(app, parser_client, context)

    doc = await _document(app, context.doc_id)
    assert doc.status == "chunking"
    assert doc.failed_step is None
    assert len(fake_parser_app.state.submissions) == 4
    records = await _records(app, context.doc_id)
    assert len(records) == 1
    assert records[0].ok is True


async def test_temporary_error_exhausts_retries(
    app, parser_client, fake_parser_app, make_ingest_document, monkeypatch
) -> None:
    """重试 3 次仍失败：文档失败、failed_step=2、错误说明是中文。"""
    monkeypatch.setattr(pipeline, "RETRY_BASE_SECONDS", 0.01)
    context = await make_ingest_document(
        parse_params={"error_code": "TIMEOUT", "temporary_fail_times": 99}
    )
    await _run(app, parser_client, context)

    doc = await _document(app, context.doc_id)
    assert doc.status == "failed"
    assert doc.failed_step == 2
    assert doc.error_code == "TIMEOUT"
    assert "超时" in (doc.error_message or "")
    assert len(fake_parser_app.state.submissions) == 4
    records = await _records(app, context.doc_id)
    assert len(records) == 1
    assert records[0].ok is False
    assert records[0].error_code == "TIMEOUT"


async def test_business_error_does_not_retry(
    app, parser_client, fake_parser_app, make_ingest_document
) -> None:
    """业务性错误（文件损坏）不重试，立即失败，失败说明用中文。"""
    context = await make_ingest_document(parse_params={"error_code": "FILE_CORRUPTED"})
    await _run(app, parser_client, context)

    doc = await _document(app, context.doc_id)
    assert doc.status == "failed"
    assert doc.failed_step == 2
    assert doc.error_code == "FILE_CORRUPTED"
    assert doc.error_message == "文件已损坏，无法解析"
    assert len(fake_parser_app.state.submissions) == 1


async def test_job_not_found_resubmits(
    app, parser_client, fake_parser_app, make_ingest_document, monkeypatch
) -> None:
    """解析服务重启导致作业 404 时重新提交，最终成功。"""
    monkeypatch.setattr(pipeline, "RETRY_BASE_SECONDS", 0.01)
    context = await make_ingest_document(parse_params={"missing_times": 1})
    await _run(app, parser_client, context)

    doc = await _document(app, context.doc_id)
    assert doc.status == "chunking"
    assert len(fake_parser_app.state.submissions) >= 2
    records = await _records(app, context.doc_id)
    assert len(records) == 1 and records[0].ok is True


async def test_cancel_by_run_id(
    app, parser_client, fake_parser_app, make_ingest_document, monkeypatch
) -> None:
    """run_id 变化后任务退出，并调用 DELETE /v1/jobs/{id} 取消解析作业。"""
    monkeypatch.setattr(pipeline, "POLL_INTERVAL_SECONDS", 0.01)
    # 让假作业慢一点，保证改 run_id 时流水线正在轮询。
    context = await make_ingest_document(parse_params={"pages": 1, "sleep_s": 5.0})
    task = asyncio.create_task(_run(app, parser_client, context))

    running = False
    for _ in range(1000):
        if any(job.status == "running" for job in fake_parser_app.state.jobs.values()):
            running = True
            break
        await asyncio.sleep(0.01)
    assert running, "假解析作业应进入运行中"
    # 等一个轮询周期，确保流水线已在轮询作业状态。
    await asyncio.sleep(0.05)

    async with app.state.sessionmaker() as session:
        await session.execute(
            sa.update(Document)
            .where(Document.id == context.doc_id)
            .values(run_id="run-2")
            .execution_options(synchronize_session=False)
        )
        await session.commit()

    with pytest.raises(RunCancelled):
        await task

    assert fake_parser_app.state.cancelled, "应调用 DELETE /v1/jobs/{id}"
    doc = await _document(app, context.doc_id)
    assert doc.status != "failed"


async def test_preview_convert_failure_does_not_fail_document(
    app, parser_client, fake_parser_app, make_ingest_document
) -> None:
    """预览 PDF 转换失败只记录日志，文档仍然解析成功（需求 4.1.8）。"""
    fake_parser_app.state.fail_convert_targets = {"pdf"}
    context = await make_ingest_document(file_type="word", ext="docx")
    await _run(app, parser_client, context)

    doc = await _document(app, context.doc_id)
    assert doc.status == "chunking"
    storage = app.state.storage
    assert not await storage.exists(doc_file_key(context.kb_id, context.doc_id, "preview.pdf"))
    assert await storage.exists(
        doc_file_key(context.kb_id, context.doc_id, "parsed/original/doc.json")
    )


async def test_old_format_converted_then_preview(
    app, parser_client, fake_parser_app, make_ingest_document
) -> None:
    """旧格式先转成 docx，Word 类另转预览 PDF，解析用转换后的文件。"""
    context = await make_ingest_document(file_type="word", ext="doc")
    await _run(app, parser_client, context)

    storage = app.state.storage
    assert await storage.exists(doc_file_key(context.kb_id, context.doc_id, "converted.docx"))
    assert await storage.exists(doc_file_key(context.kb_id, context.doc_id, "preview.pdf"))

    last = fake_parser_app.state.submissions[-1]
    assert last["source_key"].endswith("converted.docx")
    assert last["ext"] == "docx"
    assert last["file_type"] == "word"

    doc = await _document(app, context.doc_id)
    assert doc.status == "chunking"


async def test_native_word_writes_preview_only(app, parser_client, make_ingest_document) -> None:
    """原生 docx 不需要转换，只落一份预览 PDF。"""
    context = await make_ingest_document(file_type="word", ext="docx")
    await _run(app, parser_client, context)

    storage = app.state.storage
    assert await storage.exists(doc_file_key(context.kb_id, context.doc_id, "preview.pdf"))
    assert not await storage.exists(doc_file_key(context.kb_id, context.doc_id, "converted.docx"))


async def test_ppt_writes_preview(app, parser_client, make_ingest_document) -> None:
    """PPT 类文件同样落预览 PDF。"""
    context = await make_ingest_document(file_type="ppt", ext="pptx")
    await _run(app, parser_client, context)

    assert await app.state.storage.exists(
        doc_file_key(context.kb_id, context.doc_id, "preview.pdf")
    )
    doc = await _document(app, context.doc_id)
    assert doc.status == "chunking"


async def test_word_sample_has_element_refs(app, parser_client, make_ingest_document) -> None:
    """Word 固定样例用元素引用定位、没有页码（知识库技术方案 7、13）。"""
    context = await make_ingest_document(file_type="word", ext="docx")
    await _run(app, parser_client, context)

    raw = await app.state.storage.get_bytes(
        doc_file_key(context.kb_id, context.doc_id, "parsed/original/doc.json")
    )
    payload = json.loads(raw)
    refs = [item["self_ref"] for item in payload["texts"]]
    assert "#/texts/12" in refs
    assert all(item["prov"] == [] for item in payload["texts"])


async def test_main_convert_failure_fails_at_step1(
    app, parser_client, fake_parser_app, make_ingest_document
) -> None:
    """需要转换的格式转换失败时在步骤 1 失败，不进入解析。"""
    fake_parser_app.state.fail_convert_targets = {"docx"}
    context = await make_ingest_document(file_type="word", ext="doc")
    await _run(app, parser_client, context)

    doc = await _document(app, context.doc_id)
    assert doc.status == "failed"
    assert doc.failed_step == 1
    assert doc.error_code == "CONVERT_FAILED"
    assert doc.error_message
