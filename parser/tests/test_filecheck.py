"""X02：真实类型、目录大小、加密和 runner 接线。"""

from __future__ import annotations

import struct
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import olefile
import openpyxl
import pytest
from reportlab.pdfgen import canvas

from app.errors import ParseError
from app.filecheck import ENCRYPTED_MESSAGE, FileCheckSettings, check_file
from tests.conftest import submit
from tests.engines.pdf_samples import write_encrypted_pdf
from tests.test_api import poll

SAMPLES = Path(__file__).parent / "samples"


def rejected(path, ext, code, **kwargs):
    with pytest.raises(ParseError) as error:
        check_file(path, ext, **kwargs)
    assert error.value.code == code
    assert any("\u4e00" <= char <= "\u9fff" for char in error.value.message)
    return error.value


def test_executable_renamed_pdf(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"MZ" + bytes(256))
    rejected(path, "pdf", "FILE_TYPE_MISMATCH")


def test_pdf_renamed_docx(tmp_path):
    path = tmp_path / "fake.docx"
    document = canvas.Canvas(str(path))
    document.drawString(20, 20, "hello")
    document.save()
    rejected(path, "docx", "FILE_TYPE_MISMATCH")


def test_truncated_ole_document_is_corrupted(tmp_path):
    path = tmp_path / "damaged.doc"
    path.write_bytes(olefile.MAGIC + bytes(128))
    rejected(path, "doc", "FILE_CORRUPTED")


def test_zip_bomb_and_environment_limit(tmp_path, monkeypatch):
    path = tmp_path / "bomb.docx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for index in range(16):
            archive.writestr(f"zeros-{index}", bytes(1024 * 1024))
    assert path.stat().st_size < 100 * 1024
    assert FileCheckSettings().parser_archive_max_bytes == 1024**3
    monkeypatch.setenv("PARSER_ARCHIVE_MAX_BYTES", str(8 * 1024**2))
    rejected(path, "docx", "ARCHIVE_TOO_LARGE")


def test_zip_too_many_entries(tmp_path):
    path = tmp_path / "many.odt"
    with ZipFile(path, "w") as archive:
        for index in range(10_001):
            archive.writestr(str(index), b"")
    rejected(path, "odt", "ARCHIVE_TOO_LARGE")


def test_zip_encryption_flag(tmp_path):
    path = tmp_path / "encrypted.docx"
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", b"secret")
    data = bytearray(path.read_bytes())
    # ZipCrypto 标志位同时写入本地头和中央目录；检查不需要读取加密数据。
    struct.pack_into("<H", data, 6, 1)
    central = data.index(b"PK\x01\x02")
    struct.pack_into("<H", data, central + 8, 1)
    path.write_bytes(data)
    error = rejected(path, "docx", "FILE_ENCRYPTED")
    assert error.message == ENCRYPTED_MESSAGE


def test_real_office_encrypted_docx():
    assert rejected(SAMPLES / "encrypted.docx", "docx", "FILE_ENCRYPTED").message == (
        ENCRYPTED_MESSAGE
    )


def test_encrypted_pdf(tmp_path):
    path = write_encrypted_pdf(tmp_path / "encrypted.pdf")
    assert rejected(path, "pdf", "FILE_ENCRYPTED").message == ENCRYPTED_MESSAGE


@pytest.mark.parametrize("ext", ["txt", "md", "csv", "tsv", "html", "htm", "shtml", "mhtml", "mht"])
@pytest.mark.parametrize("encoding", ["gbk", "utf-8-sig", "utf-16"])
def test_text_encodings(tmp_path, ext, encoding):
    path = tmp_path / f"text.{ext}"
    path.write_bytes("中文编码测试\n第二行\t数据".encode(encoding))
    check_file(path, ext)


def test_text_does_not_decode_with_replacement(tmp_path):
    path = tmp_path / "binary.txt"
    path.write_bytes(bytes(range(256)))
    rejected(path, "txt", "FILE_CORRUPTED")


def test_zip_type_markers(tmp_path):
    path = tmp_path / "sheet.xlsx"
    openpyxl.Workbook().save(path)
    check_file(path, "xlsx")
    rejected(path, "docx", "FILE_TYPE_MISMATCH")
    rejected(path, "pdf", "FILE_TYPE_MISMATCH")


@pytest.mark.parametrize("ext", ["doc", "ppt", "xls", "docx"])
def test_real_office_types(ext):
    check_file(SAMPLES / f"sample.{ext}", ext)


@pytest.mark.parametrize(
    ("ext", "mime"),
    [("odt", "text"), ("ods", "spreadsheet"), ("odp", "presentation")],
)
def test_opendocument_markers(tmp_path, ext, mime):
    path = tmp_path / f"test.{ext}"
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", f"application/vnd.oasis.opendocument.{mime}")
        archive.writestr("content.xml", "<document/>")
    check_file(path, ext)
    rejected(path, "docx", "FILE_TYPE_MISMATCH")


def test_epub_and_ofd(tmp_path):
    path = tmp_path / "book.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
    check_file(path, "epub")
    rejected(path, "ofd", "FILE_TYPE_MISMATCH")
    with ZipFile(path, "w") as archive:
        archive.writestr("OFD.xml", "<OFD/>")
    check_file(path, "ofd")


def test_runner_rejects_before_engine(make_client, s3):
    client = make_client()
    s3.put_object(Bucket="test-bucket", Key="in/a.txt", Body=b"MZ" + bytes(256))
    job_id = submit(client, file_type="pdf", ext="pdf").json()["id"]
    status = poll(client, job_id, {"failed", "succeeded"})
    assert status["status"] == "failed"
    assert status["error"]["code"] == "FILE_TYPE_MISMATCH"
    assert status["result"] is None
    assert client.app.state.runner.get(job_id).work_dir is None
