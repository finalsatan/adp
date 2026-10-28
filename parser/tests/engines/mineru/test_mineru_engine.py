"""MinerU 引擎的档位、地址选择和可用性测试（真 HTTP，假 MinerU 服务）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from docling_core.types.doc.labels import DocItemLabel

from app.engines.base import ParseContext, RemoteConfig
from app.engines.mineru.engine import MineruEngine
from app.errors import ParseError
from tests.engines.mineru.fake_server import FakeMineru


def make_context(
    tmp_path: Path,
    *,
    file_type: str = "pdf",
    ext: str = "pdf",
    params: dict | None = None,
    remote: RemoteConfig | None = None,
    report=None,
) -> ParseContext:
    source = tmp_path / f"input.{ext}"
    source.write_bytes(b"%PDF-1.4 fake")
    return ParseContext(
        input_path=str(source),
        file_type=file_type,
        ext=ext,
        engine_params=params or {},
        preprocess={},
        table={},
        work_dir=str(tmp_path),
        report=report or (lambda done, total: None),
        remote=remote,
    )


@pytest.fixture
def server():
    with FakeMineru() as fake:
        yield fake


def test_supports_only_mineru_file_types():
    engine = MineruEngine()
    assert engine.supports("pdf", "pdf")
    assert engine.supports("image", "png")
    for file_type, ext in (
        ("word", "docx"),
        ("ppt", "pptx"),
        ("html", "html"),
        ("epub", "epub"),
        ("ofd", "ofd"),
    ):
        assert engine.supports(file_type, ext)
    # 需求 4.1.4 的引擎表里 Excel、CSV、Markdown、TXT 没有 MinerU
    for file_type, ext in (("excel", "xlsx"), ("csv", "csv"), ("markdown", "md"), ("txt", "txt")):
        assert not engine.supports(file_type, ext)


def test_pdf_basic_tier_parses(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_URL", server.url)
    monkeypatch.setenv("MINERU_API_KEY", "key")
    engine = MineruEngine(poll_interval=0)

    output = engine.parse(make_context(tmp_path, params={"tier": "basic"}))

    assert server.tiers == ["basic"]
    assert output.pages == 1
    assert output.engine_version == "mineru-4.0.12"
    texts = {item.text: item.label for item in output.document.texts}
    assert texts["假 MinerU 标题"] == DocItemLabel.SECTION_HEADER
    assert texts["假 MinerU 正文。"] == DocItemLabel.TEXT
    assert len(output.document.pictures) == 1
    # 标题级别：样例里最高是 2 级，映射成 1 级
    heading = next(item for item in output.document.texts if item.text == "假 MinerU 标题")
    assert heading.level == 1
    # 上传时带了密钥（自建服务同源）
    upload = [headers for path, headers in server.headers if path == "/v1/uploads/up1/content"]
    assert upload and upload[0]["Authorization"] == "Bearer key"


def test_pdf_default_tier_is_basic(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_URL", server.url)
    MineruEngine(poll_interval=0).parse(make_context(tmp_path))
    assert server.tiers == ["basic"]


@pytest.mark.parametrize("tier", ["flash", "basic", "standard", "advanced"])
def test_native_formats_always_use_flash(server, tmp_path, monkeypatch, tier):
    """Word、PPT、HTML、EPUB、OFD 不论传什么档位都用快速档原生解析。"""
    monkeypatch.setenv("MINERU_URL", server.url)
    engine = MineruEngine(poll_interval=0)
    context = make_context(
        tmp_path,
        file_type="word",
        ext="docx",
        params={"tier": tier},
        remote=RemoteConfig(base_url=server.url, api_key="remote-key"),
    )
    engine.parse(context)
    assert server.tiers == ["flash"]


def test_standard_without_remote_is_unavailable(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_URL", server.url)
    engine = MineruEngine(poll_interval=0)
    with pytest.raises(ParseError) as excinfo:
        engine.parse(make_context(tmp_path, params={"tier": "standard"}))
    assert excinfo.value.code == "ENGINE_UNAVAILABLE"
    assert server.requests == []


def test_advanced_without_remote_is_unavailable(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_URL", server.url)
    engine = MineruEngine(poll_interval=0)
    with pytest.raises(ParseError) as excinfo:
        engine.parse(make_context(tmp_path, params={"tier": "advanced"}))
    assert excinfo.value.code == "ENGINE_UNAVAILABLE"


def test_standard_uses_remote_service(server, tmp_path, monkeypatch):
    """标准、高级档发往 remote 里的自建 GPU 服务或官方云端 API。"""
    with FakeMineru() as remote:
        monkeypatch.setenv("MINERU_URL", server.url)
        engine = MineruEngine(poll_interval=0)
        context = make_context(
            tmp_path,
            params={"tier": "advanced"},
            remote=RemoteConfig(base_url=remote.url, api_key="remote-key"),
        )
        engine.parse(context)

    assert remote.tiers == ["advanced"]
    assert server.requests == []
    upload = [headers for path, headers in remote.headers if path == "/v1/uploads/up1/content"]
    assert upload and upload[0]["Authorization"] == "Bearer remote-key"


def test_invalid_tier_is_invalid_params(server, tmp_path, monkeypatch):
    """档位取值不对是参数错误：后端不能当临时错误重试（X17）。"""
    monkeypatch.setenv("MINERU_URL", server.url)
    engine = MineruEngine(poll_interval=0)
    with pytest.raises(ParseError) as excinfo:
        engine.parse(make_context(tmp_path, params={"tier": "ultra"}))
    assert excinfo.value.code == "INVALID_PARAMS"
    assert "ultra" in excinfo.value.message


def test_missing_local_service_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("MINERU_URL", raising=False)
    engine = MineruEngine(poll_interval=0)
    with pytest.raises(ParseError) as excinfo:
        engine.parse(make_context(tmp_path))
    assert excinfo.value.code == "ENGINE_UNAVAILABLE"


def test_job_not_found_is_remote_error(server, tmp_path, monkeypatch):
    """mineru 服务重启导致作业丢失：失败成 REMOTE_ERROR，说明可以重试。"""
    monkeypatch.setenv("MINERU_URL", server.url)
    server.job_not_found = True
    engine = MineruEngine(poll_interval=0)
    with pytest.raises(ParseError) as excinfo:
        engine.parse(make_context(tmp_path))
    assert excinfo.value.code == "REMOTE_ERROR"
    assert "重启" in excinfo.value.message


def test_progress_reaches_final_page_count(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_URL", server.url)
    seen: list[tuple[int, int]] = []
    engine = MineruEngine(poll_interval=0)
    engine.parse(make_context(tmp_path, report=lambda done, total: seen.append((done, total))))
    assert seen[-1] == (1, 1)


def test_availability(server, tmp_path, monkeypatch):
    monkeypatch.delenv("MINERU_URL", raising=False)
    assert MineruEngine().availability() == (False, "未部署")

    monkeypatch.setenv("MINERU_URL", "http://127.0.0.1:1")
    assert MineruEngine().availability() == (False, "连接失败")

    monkeypatch.setenv("MINERU_URL", server.url)
    assert MineruEngine().availability() == (True, None)


def test_availability_is_cached(server, monkeypatch):
    monkeypatch.setenv("MINERU_URL", server.url)
    engine = MineruEngine()
    assert engine.availability() == (True, None)
    before = len(server.requests)
    assert engine.availability() == (True, None)
    assert len(server.requests) == before


def test_oversized_file_reports_file_too_large(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_URL", server.url)
    monkeypatch.setattr("app.engines.mineru.client.MAX_FILE_BYTES", 1)
    with pytest.raises(ParseError) as excinfo:
        MineruEngine().parse(make_context(tmp_path))
    assert excinfo.value.code == "FILE_TOO_LARGE"
    assert excinfo.value.message == "文件超过 MinerU 单文件上限 200 MiB"
    assert server.requests == []
