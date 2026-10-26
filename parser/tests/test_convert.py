"""X02：转换接口、真实 LibreOffice 转换、常驻进程与超时恢复。"""

from __future__ import annotations

import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import pypdfium2 as pdfium
import pytest

from app.convert import Converter
from app.errors import AppError
from tests.conftest import AUTH

SAMPLES = Path(__file__).parent / "samples"


def request_convert(client, source_key="in/sample.docx", source_ext="docx", target="pdf", **extra):
    return client.post(
        "/v1/convert",
        headers=AUTH,
        json={
            "source_key": source_key,
            "source_ext": source_ext,
            "target": target,
            "output_key": "out/converted",
            **extra,
        },
    )


def upload_sample(s3, ext):
    key = f"in/sample.{ext}"
    s3.put_object(Bucket="test-bucket", Key=key, Body=(SAMPLES / f"sample.{ext}").read_bytes())
    return key


def test_convert_auth(make_client):
    client = make_client()
    assert client.post("/v1/convert", json={}).status_code == 401
    assert (
        client.post("/v1/convert", json={}, headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )


def test_bad_source_returns_convert_failed(make_client, s3):
    client = make_client()
    s3.put_object(Bucket="test-bucket", Key="in/sample.docx", Body=b"corrupt Office file")
    response = request_convert(client)
    assert response.status_code == 422
    assert response.json()["code"] == "CONVERT_FAILED"
    assert response.json()["message"]
    assert response.json()["request_id"]
    assert s3.list_objects_v2(Bucket="test-bucket", Prefix="out/")["KeyCount"] == 0


def test_unsupported_conversion(make_client):
    client = make_client()
    response = request_convert(client, source_ext="xls", target="pdf")
    assert response.status_code == 422
    assert response.json()["code"] == "CONVERT_FAILED"


def test_storage_failure_does_not_leak_details(make_client):
    client = make_client()
    response = request_convert(client, source_key="missing-secret-key")
    assert response.status_code == 422
    assert response.json()["code"] == "CONVERT_FAILED"
    assert "missing-secret-key" not in response.text


@pytest.mark.libreoffice
@pytest.mark.parametrize(
    ("source_ext", "target", "main"),
    [
        ("doc", "docx", "word/document.xml"),
        ("ppt", "pptx", "ppt/presentation.xml"),
        ("xls", "xlsx", "xl/workbook.xml"),
        ("rtf", "docx", "word/document.xml"),
        ("odt", "docx", "word/document.xml"),
        ("odp", "pptx", "ppt/presentation.xml"),
        ("ods", "xlsx", "xl/workbook.xml"),
    ],
)
def test_legacy_conversion(make_client, s3, tmp_path, source_ext, target, main):
    client = make_client()
    key = upload_sample(s3, source_ext)
    response = request_convert(client, key, source_ext, target)
    assert response.status_code == 200, response.text
    result = response.json()
    data = s3.get_object(Bucket="test-bucket", Key=result["output_key"])["Body"].read()
    assert result["size"] == len(data) > 0
    assert 0 <= result["duration_ms"] < 120_000
    path = tmp_path / f"output.{target}"
    path.write_bytes(data)
    with ZipFile(path) as archive:
        assert main in archive.namelist()


@pytest.mark.libreoffice
def test_chinese_pdf_and_five_conversions_reuse_one_server(make_client, s3, tmp_path):
    client = make_client()
    upload_sample(s3, "docx")
    converter = client.app.state.converter
    pid = converter.pid
    for index in range(5):
        response = request_convert(client, output_key=f"out/preview-{index}.pdf")
        assert response.status_code == 200, response.text
        assert converter.starts == 1
        assert converter.pid == pid
    result = response.json()
    path = tmp_path / "preview.pdf"
    path.write_bytes(s3.get_object(Bucket="test-bucket", Key=result["output_key"])["Body"].read())
    with pdfium.PdfDocument(path) as document:
        page = document[0]
        text = page.get_textpage()
        try:
            assert "智能体开发平台中文预览测试" in text.get_text_range()
            # 实际渲染也成功，而非只有文字映射表。
            assert page.render(scale=0.5).to_pil().getbbox() is not None
        finally:
            text.close()
            page.close()
    assert b"/FontFile" in path.read_bytes()  # PDF 内嵌了字体，可离线预览。


@pytest.mark.libreoffice
def test_other_preview_formats(make_client, s3, tmp_path):
    client = make_client()
    for ext in ["doc", "rtf", "odt", "ppt", "pptx", "odp"]:
        if ext == "pptx":
            key = upload_sample(s3, "ppt")
            converted = request_convert(client, key, "ppt", "pptx", output_key="in/sample.pptx")
            assert converted.status_code == 200, converted.text
            key = "in/sample.pptx"
        else:
            key = upload_sample(s3, ext)
        response = request_convert(client, key, ext, "pdf")
        assert response.status_code == 200, (ext, response.text)
        path = tmp_path / f"{ext}.pdf"
        path.write_bytes(s3.get_object(Bucket="test-bucket", Key="out/converted")["Body"].read())
        with pdfium.PdfDocument(path) as document:
            assert len(document) >= 1


@pytest.mark.libreoffice
def test_timeout_restarts_then_recovers(make_client, s3):
    client = make_client()
    upload_sample(s3, "docx")
    converter = client.app.state.converter
    old_pid = converter.pid
    converter.timeout_s = 0.01
    response = request_convert(client)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "CONVERT_FAILED"
    assert "超时" in response.json()["message"]
    assert converter.pid != old_pid
    assert converter.starts == 2
    converter.timeout_s = 120
    assert request_convert(client).status_code == 200


@pytest.mark.libreoffice
def test_crashed_server_is_automatically_restarted(make_client, s3):
    client = make_client()
    upload_sample(s3, "docx")
    assert request_convert(client).status_code == 200
    converter = client.app.state.converter
    old_pid = converter.pid
    os.kill(old_pid, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while converter.pid == old_pid and time.monotonic() < deadline:
        time.sleep(0.1)
    assert converter.pid != old_pid
    assert request_convert(client).status_code == 200


@pytest.mark.libreoffice
def test_parallel_requests_are_serialized(make_client, s3, monkeypatch):
    from app import convert

    client = make_client()
    upload_sample(s3, "docx")
    active = 0
    peak = 0
    original = convert.subprocess.run

    def tracked(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        try:
            return original(*args, **kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(convert.subprocess, "run", tracked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda index: request_convert(client, output_key=f"out/{index}"), [1, 2])
        )
    assert all(response.status_code == 200 for response in responses)
    assert peak == 1
    assert client.app.state.converter.starts == 1


def test_queue_wait_has_timeout(tmp_path):
    converter = Converter(timeout_s=0.01)
    converter._lock.acquire()
    try:
        with pytest.raises(AppError) as error:
            converter.convert_file(tmp_path / "input.doc", tmp_path / "output.docx")
        assert error.value.code == "CONVERT_FAILED"
        assert "超时" in error.value.message
    finally:
        converter._lock.release()


def test_samples_under_100kb():
    for path in SAMPLES.iterdir():
        if path.is_file():
            assert path.stat().st_size <= 100 * 1024
