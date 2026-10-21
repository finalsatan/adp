from __future__ import annotations

from adp_parser.extensions import (
    OcrPage,
    register_ocr_provider,
    unregister_ocr_provider,
)
from samples import make_blank_pdf, make_encrypted_pdf, make_pdf, make_pdf_table


def test_pdf_page_heading_and_paragraph(parse_bytes, client):
    response = parse_bytes(client, "sample.pdf", make_pdf())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["engine"] == "pdfplumber"
    assert any("启发式" in warning for warning in payload["warnings"])

    elements = payload["elements"]
    heading = elements[0]
    assert heading["type"] == "heading"
    assert heading["text"] == "Large Heading"
    assert heading["level"] == 1
    assert heading["page"] == 1
    assert heading["headingPath"] == ["Large Heading"]

    first_paragraph = elements[1]
    assert first_paragraph["type"] == "paragraph"
    assert first_paragraph["page"] == 1
    assert "This is body text on page one." in first_paragraph["text"]
    assert "It continues on the next line." in first_paragraph["text"]

    page_two = next(element for element in elements if element["page"] == 2)
    assert page_two["text"] == "Page two body."


def test_pdf_table_header_and_rows_with_page(parse_bytes, client):
    response = parse_bytes(client, "table.pdf", make_pdf_table())
    assert response.status_code == 200, response.text
    payload = response.json()
    elements = payload["elements"]
    header = next(element for element in elements if element["type"] == "table_header")
    rows = [element for element in elements if element["type"] == "table_row"]
    assert header["text"] == "Name | Age"
    assert header["page"] == 1
    assert header["row"] == 1
    assert [row["text"] for row in rows] == ["Alice | 30", "Bob | 25"]
    assert [row["row"] for row in rows] == [2, 3]
    assert all(row["page"] == 1 for row in rows)


def test_scanned_pdf_fails_with_ocr_hint(parse_bytes, client):
    response = parse_bytes(client, "scan.pdf", make_blank_pdf())
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "scanned_pdf_no_text"
    assert "OCR" in payload["message"]
    assert "带文本层" in payload["message"]


def test_encrypted_pdf_fails_readably(parse_bytes, client):
    response = parse_bytes(client, "secret.pdf", make_encrypted_pdf())
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "pdf_encrypted"
    assert "加密" in payload["message"]


def test_registered_ocr_provider_extension(parse_bytes, make_client):
    class DummyProvider:
        name = "dummy-ocr"
        kind = "ocr"

        def extract(self, data, file_name, mime):
            assert data
            assert file_name == "scan.pdf"
            return [OcrPage(page=1, text="Text recovered by OCR.")]

    register_ocr_provider(DummyProvider())
    try:
        client = make_client()
        response = parse_bytes(client, "scan.pdf", make_blank_pdf())
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["engine"] == "pdfplumber+dummy-ocr"
        assert payload["elements"][0]["text"] == "Text recovered by OCR."
        assert payload["elements"][0]["page"] == 1
        assert any("dummy-ocr" in warning for warning in payload["warnings"])

        engines = client.get("/v1/engines").json()
        assert engines["ocr"]["available"] is True
        assert engines["ocr"]["providers"] == [{"name": "dummy-ocr", "kind": "ocr"}]
    finally:
        unregister_ocr_provider("dummy-ocr")
