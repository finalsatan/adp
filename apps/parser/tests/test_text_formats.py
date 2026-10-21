from __future__ import annotations


def test_txt_paragraphs(parse_text, client):
    response = parse_text(
        client, "notes.txt", "First paragraph line one.\nline two.\n\nSecond paragraph."
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schemaVersion"] == 1
    assert payload["engine"] == "plain-text"
    assert [element["type"] for element in payload["elements"]] == [
        "paragraph",
        "paragraph",
    ]
    assert payload["elements"][0]["paragraph"] == 1
    assert payload["elements"][1]["paragraph"] == 2
    assert "First paragraph line one." in payload["text"]
    assert payload["warnings"] == []


def test_markdown_headings_list_code_and_table(parse_text, client):
    markdown = """---
title: ignored metadata
---

# Top *Title*

Intro with [link](https://example.com) and `code`.

## Section

- first
- second

```python
print("hello")
```

| Name | Age |
| --- | ---: |
| Alice | 30 |
| Bob | 25 |
"""
    response = parse_text(client, "guide.md", markdown)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["engine"] == "markdown-lite"
    assert any("front matter" in warning for warning in payload["warnings"])

    elements = payload["elements"]
    assert elements[0]["type"] == "heading"
    assert elements[0]["level"] == 1
    assert elements[0]["text"] == "Top Title"
    assert elements[0]["headingPath"] == ["Top Title"]

    section = next(element for element in elements if element["text"] == "Section")
    assert section["type"] == "heading"
    assert section["level"] == 2
    assert section["headingPath"] == ["Top Title", "Section"]

    item = next(element for element in elements if element["text"] == "first")
    assert item["type"] == "list_item"
    assert item["level"] == 1
    assert item["headingPath"] == ["Top Title", "Section"]

    code = next(element for element in elements if element["type"] == "code")
    assert 'print("hello")' in code["text"]

    headers = [e for e in elements if e["type"] == "table_header"]
    rows = [e for e in elements if e["type"] == "table_row"]
    assert len(headers) == 1
    assert headers[0]["text"] == "Name | Age"
    assert headers[0]["row"] == 1
    assert [row["text"] for row in rows] == ["Alice | 30", "Bob | 25"]
    assert [row["row"] for row in rows] == [2, 3]


def test_html_structure_and_ignored_scripts(parse_text, client):
    html = """<!doctype html>
<html><head><title>Meta</title><style>.x{}</style></head>
<body>
  <h1>Page Title</h1>
  <p>Intro paragraph.</p>
  <h2>Details</h2>
  <ul><li>One</li><li>Two<ul><li>Nested</li></ul></li></ul>
  <table>
    <caption>People</caption>
    <thead><tr><th>Name</th><th>Age</th></tr></thead>
    <tbody><tr><td>Alice</td><td>30</td></tr></tbody>
  </table>
  <script>alert('ignored')</script>
</body></html>"""
    response = parse_text(client, "page.html", html)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["engine"] == "beautifulsoup4"
    assert "Meta" not in payload["text"]
    assert "ignored" not in payload["text"]

    elements = payload["elements"]
    heading = elements[0]
    assert heading == {
        "id": "e1",
        "type": "heading",
        "text": "Page Title",
        "order": 1,
        "level": 1,
        "headingPath": ["Page Title"],
    }
    nested = next(element for element in elements if element["text"] == "Nested")
    assert nested["type"] == "list_item"
    assert nested["level"] == 2
    header = next(element for element in elements if element["type"] == "table_header")
    row = next(element for element in elements if element["type"] == "table_row")
    assert header["text"] == "Name | Age"
    assert header["row"] == 1
    assert row["text"] == "Alice | 30"
    assert row["row"] == 2
    assert row["headingPath"] == ["Page Title", "Details"]


def test_csv_delimiter_and_row_numbers(parse_text, client):
    response = parse_text(
        client,
        "people.csv",
        "Name;Age\nAlice;30\n\nBob;25\n",
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["engine"] == "python-csv"
    assert payload["elements"][0]["type"] == "table_header"
    assert payload["elements"][0]["row"] == 1
    assert payload["elements"][0]["text"] == "Name | Age"
    assert [element["row"] for element in payload["elements"]] == [1, 2, 4]
    assert any("分隔符" in warning for warning in payload["warnings"])


def test_gb18030_text_decode(parse_bytes, client):
    data = "中文段落一\n\n中文段落二".encode("gb18030")
    response = parse_bytes(client, "legacy.txt", data)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["elements"][0]["text"] == "中文段落一"
    assert any("UTF-8" in warning for warning in payload["warnings"])
