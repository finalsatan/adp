from __future__ import annotations

import base64
import io
import time
import zipfile

import app as app_module
from adp_parser.config import Settings


def test_health_and_engines(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["schemaVersion"] == 1

    engines = client.get("/v1/engines")
    assert engines.status_code == 200
    payload = engines.json()
    extensions = {item["extension"] for item in payload["formats"]}
    assert extensions == {".txt", ".md", ".csv", ".html", ".pdf", ".docx", ".pptx", ".xlsx"}
    assert payload["ocr"]["available"] is False
    assert payload["ocr"]["providers"] == []
    assert payload["limits"]["maxInputBytes"] == 20 * 1024 * 1024


def test_token_auth_is_enforced_when_configured(make_client):
    client = make_client(Settings(token="parser-secret", timeout_seconds=15))
    assert client.get("/health").status_code == 200
    assert client.get("/health").json()["authRequired"] is True

    assert client.get("/v1/engines").status_code == 401
    response = client.post(
        "/v1/parse", json={"fileName": "a.txt", "text": "hello"}
    )
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
    assert "X-Parser-Token" in response.json()["message"]

    assert client.get("/v1/engines", headers={"X-Parser-Token": "wrong"}).status_code == 401
    response = client.post(
        "/v1/parse",
        json={"fileName": "a.txt", "text": "hello"},
        headers={"X-Parser-Token": "parser-secret"},
    )
    assert response.status_code == 200, response.text


def test_request_validation_errors_are_readable(client):
    response = client.post("/v1/parse", json={"fileName": "a.txt"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "dataBase64" in response.json()["message"]

    response = client.post(
        "/v1/parse",
        json={"fileName": "a.txt", "text": "hello", "dataBase64": "aGVsbG8="},
    )
    assert response.status_code == 400
    assert "不能同时" in response.json()["message"]

    response = client.post("/v1/parse", json={"text": "hello"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert "fileName" in response.json()["message"]

    response = client.post(
        "/v1/parse", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"

    response = client.post(
        "/v1/parse", content=b"{}", headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415
    assert response.json()["error"] == "invalid_content_type"


def test_unsupported_extension_mime_and_content_mismatch(client):
    response = client.post(
        "/v1/parse", json={"fileName": "a.exe", "text": "hello"}
    )
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_file_type"

    response = client.post(
        "/v1/parse",
        json={"fileName": "a.txt", "text": "hello", "mime": "application/pdf"},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "mime_mismatch"

    response = client.post(
        "/v1/parse",
        json={
            "fileName": "a.pdf",
            "dataBase64": base64.b64encode(b"not a pdf").decode("ascii"),
            "mime": "application/pdf",
        },
    )
    assert response.status_code == 415
    assert response.json()["error"] == "content_mismatch"

    response = client.post(
        "/v1/parse",
        json={
            "fileName": "a.docx",
            "dataBase64": base64.b64encode(b"plain text cannot be a docx").decode("ascii"),
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    )
    assert response.status_code == 415
    assert response.json()["error"] == "invalid_zip"


def test_binary_formats_reject_text_field(client):
    response = client.post(
        "/v1/parse", json={"fileName": "a.docx", "text": "hello"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "text_input_not_supported"


def test_base64_is_strict(client):
    for value in ["abc", "a===", "!!!!", "YQ="]:
        response = client.post(
            "/v1/parse", json={"fileName": "a.txt", "dataBase64": value}
        )
        assert response.status_code == 400, (value, response.text)
        assert response.json()["error"] == "invalid_base64"

    response = client.post(
        "/v1/parse", json={"fileName": "a.txt", "dataBase64": "YR=="}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_base64"

    encoded = "data:text/plain;base64," + base64.b64encode(b"hello").decode()
    response = client.post(
        "/v1/parse", json={"fileName": "a.txt", "dataBase64": encoded}
    )
    assert response.status_code == 200
    assert response.json()["elements"][0]["text"] == "hello"


def test_generic_mime_and_uppercase_extension(client):
    response = client.post(
        "/v1/parse",
        json={
            "fileName": "NOTES.TXT",
            "text": "hello",
            "mime": "application/octet-stream",
        },
    )
    assert response.status_code == 200
    assert response.json()["elements"][0]["text"] == "hello"


def test_file_name_is_not_a_path(client):
    response = client.post(
        "/v1/parse",
        json={"fileName": "../../../../etc/passwd.txt", "text": "content only"},
    )
    assert response.status_code == 200
    assert response.json()["elements"][0]["text"] == "content only"


def test_input_size_limits(small_limits_client):
    client = small_limits_client
    response = client.post(
        "/v1/parse", json={"fileName": "a.txt", "text": "x" * (1024 * 1024 + 1)}
    )
    assert response.status_code == 413
    assert response.json()["error"] == "file_too_large"

    data = b"x" * (1024 * 1024 + 1)
    response = client.post(
        "/v1/parse",
        json={
            "fileName": "a.txt",
            "dataBase64": base64.b64encode(data).decode("ascii"),
        },
    )
    assert response.status_code == 413
    assert response.json()["error"] == "file_too_large"


def test_payload_size_limit_is_enforced_before_json_parsing(small_limits_client):
    client = small_limits_client
    response = client.post(
        "/v1/parse",
        content=b"x" * (3 * 1024 * 1024),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


def test_zip_bomb_ratio_is_rejected(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/bomb.bin", b"\0" * (2 * 1024 * 1024))
    response = client.post(
        "/v1/parse",
        json={
            "fileName": "bomb.docx",
            "dataBase64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    )
    assert response.status_code == 413
    assert response.json()["error"] == "zip_limit_exceeded"


def test_office_dtd_and_path_traversal_are_rejected(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><document/>',
        )
    response = client.post(
        "/v1/parse",
        json={
            "fileName": "dtd.docx",
            "dataBase64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unsafe_xml"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../evil.xml", "<x/>")
        archive.writestr("word/document.xml", "<document/>")
    response = client.post(
        "/v1/parse",
        json={
            "fileName": "traversal.docx",
            "dataBase64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unsafe_archive"


def test_timeout_returns_504(make_client, monkeypatch):
    def slow_parse(**kwargs):
        time.sleep(0.3)
        raise AssertionError("timeout test should not use this result")

    monkeypatch.setattr(app_module, "parse_request", slow_parse)
    client = make_client(Settings(token=None, timeout_seconds=0.05))
    response = client.post(
        "/v1/parse", json={"fileName": "a.txt", "text": "hello"}
    )
    assert response.status_code == 504
    assert response.json()["error"] == "parse_timeout"
    assert "超时" in response.json()["message"]
