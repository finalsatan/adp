from __future__ import annotations

from samples import make_docx, make_pptx, make_xlsx


def test_docx_headings_paragraphs_and_table(parse_bytes, client):
    response = parse_bytes(client, "sample.docx", make_docx())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["engine"] == "python-docx"

    elements = payload["elements"]
    assert elements[0]["type"] == "heading"
    assert elements[0]["level"] == 1
    assert elements[0]["text"] == "Chapter One"
    assert elements[0]["paragraph"] == 1
    assert "page" not in elements[0]

    paragraph = next(element for element in elements if element["text"] == "First body paragraph.")
    assert paragraph["type"] == "paragraph"
    assert paragraph["paragraph"] == 2
    assert paragraph["headingPath"] == ["Chapter One"]

    section = next(element for element in elements if element["text"] == "Section A")
    assert section["type"] == "heading"
    assert section["level"] == 2
    assert section["headingPath"] == ["Chapter One", "Section A"]

    bullet = next(element for element in elements if element["text"] == "Bullet item")
    assert bullet["type"] == "list_item"
    assert bullet["level"] == 1
    assert bullet["headingPath"] == ["Chapter One", "Section A"]

    header = next(element for element in elements if element["type"] == "table_header")
    rows = [element for element in elements if element["type"] == "table_row"]
    assert header["text"] == "Name | Age"
    assert header["row"] == 1
    assert [row["text"] for row in rows] == ["Alice | 30", "Bob | 25"]
    assert [row["row"] for row in rows] == [2, 3]


def test_pptx_slide_page_paragraph_and_table(parse_bytes, client):
    response = parse_bytes(client, "sample.pptx", make_pptx())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["engine"] == "python-pptx"

    elements = payload["elements"]
    title = elements[0]
    assert title["type"] == "heading"
    assert title["level"] == 1
    assert title["text"] == "Slide Title"
    assert title["page"] == 1
    assert title["paragraph"] == 1
    assert title["headingPath"] == ["Slide Title"]

    first = next(element for element in elements if element["text"] == "First paragraph")
    assert first["type"] == "paragraph"
    assert first["page"] == 1
    assert first["paragraph"] == 2
    assert first["headingPath"] == ["Slide Title"]

    header = next(element for element in elements if element["type"] == "table_header")
    row = next(element for element in elements if element["type"] == "table_row")
    assert header["text"] == "Column A | Column B"
    assert header["page"] == 1
    assert header["row"] == 1
    assert row["text"] == "1 | 2"
    assert row["row"] == 2


def test_xlsx_sheet_row_and_header(parse_bytes, client):
    response = parse_bytes(client, "sample.xlsx", make_xlsx())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["engine"] == "openpyxl"
    assert any("Empty" in warning for warning in payload["warnings"])

    elements = payload["elements"]
    heading = elements[0]
    assert heading["type"] == "heading"
    assert heading["text"] == "Data"
    assert heading["sheet"] == "Data"
    assert heading["level"] == 1
    assert heading["headingPath"] == ["Data"]

    assert elements[1]["type"] == "table_header"
    assert elements[1]["text"] == "Name | Age"
    assert elements[1]["sheet"] == "Data"
    assert elements[1]["row"] == 1
    assert [element["row"] for element in elements[2:]] == [2, 3]
    assert [element["text"] for element in elements[2:]] == ["Alice | 30", "Bob | 25"]
