"""内置入库方案、解析引擎列表与方案校验（P3-06）。"""

from __future__ import annotations

import copy

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EngineUnavailableError, ValidationError
from app.core.schemas import Role
from app.kb import schemes
from app.kb.parser_client import ParserClient
from app.kb.schemas import (
    FileType,
    MineruTier,
    ParseEngine,
    SchemeConfig,
)
from app.kb.schemes import BUILTIN_SCHEMES
from app.kb.schemes_router import get_parser_client
from app.kb.tables import IngestScheme
from tests.factories import auth_cookies, make_org, make_user

# 卡片“要做 1”的固定 ID、名称与文件类型；测试用硬编码的期望值核对迁移数据。
EXPECTED_BUILTIN: dict[int, tuple[str, str]] = {
    1: ("PDF · 通用", "pdf"),
    2: ("PDF · 扫描件", "pdf"),
    3: ("Word · 通用", "word"),
    4: ("PPT · 按页", "ppt"),
    5: ("Excel · 表格", "excel"),
    6: ("CSV · 通用", "csv"),
    7: ("Markdown · 通用", "markdown"),
    8: ("TXT · 通用", "txt"),
    9: ("HTML · 通用", "html"),
    10: ("EPUB · 通用", "epub"),
    11: ("OFD · 通用", "ofd"),
    12: ("图片 · OCR", "image"),
}


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def seed_builtin_schemes(session: AsyncSession) -> None:
    """把迁移写入的内置方案补进测试库：conftest 每个测试后 TRUNCATE，迁移数据会丢。

    会话开始时迁移可能已经把数据写进去了，所以先按固定 ID 删掉再插，保证幂等。
    """
    ids = [row["id"] for row in BUILTIN_SCHEMES]
    await session.execute(sa.delete(IngestScheme).where(IngestScheme.id.in_(ids)))
    for row in BUILTIN_SCHEMES:
        session.add(
            IngestScheme(
                id=row["id"],
                builtin=True,
                resource_id=None,
                file_type=row["file_type"],
                name=row["name"],
                description=row["description"],
                config=row["config"],
                version=1,
            )
        )
    await session.flush()
    await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def _builtin_schemes(session: AsyncSession) -> None:
    await seed_builtin_schemes(session)


@pytest_asyncio.fixture
async def builder(session: AsyncSession):
    org = await make_org(session, "方案组织")
    return await make_user(session, Role.builder, org, username="scheme_builder")


async def test_builtin_schemes_data(session: AsyncSession) -> None:
    """迁移写入的 12 个方案：ID、名称、文件类型与卡片一致，config 能被 SchemeConfig 解析。"""
    rows = (
        (await session.execute(sa.select(IngestScheme).order_by(IngestScheme.id))).scalars().all()
    )
    assert {row.id: (row.name, row.file_type) for row in rows} == EXPECTED_BUILTIN
    assert {row.file_type for row in rows} == {file_type.value for file_type in FileType}
    for row in rows:
        assert row.builtin is True
        assert row.resource_id is None
        assert row.version == 1
        assert SchemeConfig.model_validate(row.config).file_type.value == row.file_type


async def test_list_schemes_and_filter(client: AsyncClient, session: AsyncSession, builder) -> None:
    login_as(client, builder)

    listing = await client.get("/api/v1/schemes")
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 12

    pdf = await client.get("/api/v1/schemes", params={"file_type": "pdf"})
    assert pdf.status_code == 200
    assert pdf.json()["total"] == 2
    assert {item["id"] for item in pdf.json()["items"]} == {1, 2}

    builtin = await client.get("/api/v1/schemes", params={"builtin": False})
    assert builtin.json()["total"] == 0

    keyword = await client.get("/api/v1/schemes", params={"keyword": "扫描"})
    assert keyword.json()["total"] == 1
    assert keyword.json()["items"][0]["id"] == 2

    paged = await client.get("/api/v1/schemes", params={"page": 2, "page_size": 5})
    assert paged.json()["total"] == 12
    assert len(paged.json()["items"]) == 5


async def test_scheme_detail(client: AsyncClient, session: AsyncSession, builder) -> None:
    login_as(client, builder)

    detail = await client.get("/api/v1/schemes/1")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["name"] == "PDF · 通用"
    assert body["file_type"] == "pdf"
    assert body["builtin"] is True
    assert body["org"] is None and body["owner"] is None
    assert body["visibility"] == "platform"
    assert body["status"] == "enabled"
    assert body["version"] == 1
    assert body["default_kb_count"] == 0
    # config 能被 SchemeConfig 反序列化，默认值照需求 4.1.4。
    config = SchemeConfig.model_validate(body["config"])
    assert config.parse.engine == ParseEngine.docling
    assert config.parse.scan_mode == "auto"
    assert config.chunk.max_tokens == 800
    assert config.chunk.min_tokens == 50
    assert config.chunk.parent_child.enabled is False
    assert config.enhance.context is False

    missing = await client.get("/api/v1/schemes/9999")
    assert missing.status_code == 404


async def test_default_kb_count(session: AsyncSession) -> None:
    from app.kb.tables import KbDefaultScheme, KnowledgeBase
    from app.models.tables import Model
    from app.resources.tables import Resource
    from tests.factories import make_org

    org = await make_org(session, "计数组织")
    user = await make_user(session, Role.builder, org, username="count_builder")
    resource = Resource(type="kb", name="计数库", org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    model = Model(
        name="计数模型",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=1024,
        created_by=user.id,
    )
    session.add(model)
    await session.flush()
    session.add(
        KnowledgeBase(
            id=resource.id,
            kind="doc",
            embedding_model_id=model.id,
            embedding_dim=1024,
            index_table=f"kb_index.kb_{resource.id}",
        )
    )
    await session.flush()
    session.add(KbDefaultScheme(kb_id=resource.id, file_type="pdf", scheme_id=1))
    await session.commit()

    out = await schemes.get_scheme(session, 1)
    assert out.default_kb_count == 1
    assert (await schemes.get_scheme(session, 2)).default_kb_count == 0


async def test_resolve_scheme_id(session: AsyncSession) -> None:
    from app.kb.tables import KbDefaultScheme, KnowledgeBase
    from app.models.tables import Model
    from app.resources.tables import Resource
    from tests.factories import make_org

    org = await make_org(session, "解析组织")
    user = await make_user(session, Role.builder, org, username="resolve_builder")
    resource = Resource(type="kb", name="解析库", org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    model = Model(
        name="解析模型",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=1024,
        created_by=user.id,
    )
    session.add(model)
    await session.flush()
    session.add(
        KnowledgeBase(
            id=resource.id,
            kind="doc",
            embedding_model_id=model.id,
            embedding_dim=1024,
            index_table=f"kb_index.kb_{resource.id}",
        )
    )
    await session.flush()
    await session.commit()

    # 没设默认：用该文件类型的第一个内置方案。
    assert await schemes.resolve_scheme_id(session, resource.id, FileType.pdf) == 1
    assert await schemes.resolve_scheme_id(session, resource.id, FileType.word) == 3

    session.add(KbDefaultScheme(kb_id=resource.id, file_type="pdf", scheme_id=2))
    await session.commit()
    assert await schemes.resolve_scheme_id(session, resource.id, FileType.pdf) == 2


def test_validate_scheme_config_engine_support() -> None:
    # 合法：docling + pdf。
    valid = schemes.validate_scheme_config(BUILTIN_SCHEMES[0]["config"])
    assert valid.file_type == FileType.pdf

    # markitdown 不支持 pdf。
    bad = copy.deepcopy(BUILTIN_SCHEMES[0]["config"])
    bad["parse"]["engine"] = ParseEngine.markitdown.value
    with pytest.raises(EngineUnavailableError) as excinfo:
        schemes.validate_scheme_config(bad)
    assert excinfo.value.code == "ENGINE_UNAVAILABLE"

    # 引擎支持但文件类型不符（pypdfium 只支持 pdf）。
    mismatch = copy.deepcopy(BUILTIN_SCHEMES[2]["config"])  # word
    mismatch["parse"]["engine"] = ParseEngine.pypdfium.value
    with pytest.raises(EngineUnavailableError):
        schemes.validate_scheme_config(mismatch)


def test_validate_scheme_config_bounds_and_enhance() -> None:
    # 分块参数越界（max_tokens 必须 >= 1）。
    out_of_range = copy.deepcopy(BUILTIN_SCHEMES[0]["config"])
    out_of_range["chunk"]["max_tokens"] = 0
    with pytest.raises(ValidationError) as excinfo:
        schemes.validate_scheme_config(out_of_range)
    assert excinfo.value.code == "VALIDATION_ERROR"

    # 开启增强但没选对话模型。
    enhance = copy.deepcopy(BUILTIN_SCHEMES[0]["config"])
    enhance["enhance"]["context"] = True
    enhance["enhance"]["chat_model_id"] = None
    with pytest.raises(ValidationError) as excinfo:
        schemes.validate_scheme_config(enhance)
    assert excinfo.value.detail == {"field": "enhance.chat_model_id"}

    # 选了对话模型就通过。
    enhance["enhance"]["chat_model_id"] = 1
    assert schemes.validate_scheme_config(enhance).enhance.context is True


def _parser_client(app, handler) -> ParserClient:
    return ParserClient(app.state.settings, transport=httpx.MockTransport(handler))


async def test_parse_engines_parser_unreachable(
    app, client: AsyncClient, session: AsyncSession, builder
) -> None:
    login_as(client, builder)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    parser = _parser_client(app, handler)
    app.dependency_overrides[get_parser_client] = lambda: parser
    try:
        response = await client.get("/api/v1/parse-engines")
    finally:
        app.dependency_overrides.pop(get_parser_client, None)
        await parser.aclose()

    assert response.status_code == 200, response.text
    engines = response.json()
    assert len(engines) == len(ParseEngine)
    assert all(engine["available"] is False for engine in engines)
    assert all(engine["reason"] == "解析服务不可用" for engine in engines)
    # 未注册的引擎也在列表里，文件类型用需求 4.1.4 的对照表。
    by_name = {engine["name"]: engine for engine in engines}
    assert by_name["builtin"]["file_types"] == ["excel", "csv", "markdown", "txt", "html"]
    assert by_name["paddleocr"]["file_types"] == ["pdf", "image"]


async def test_parse_engines_from_parser(
    app, client: AsyncClient, session: AsyncSession, builder
) -> None:
    login_as(client, builder)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "name": "builtin",
                    "file_types": ["txt", "csv", "excel"],
                    "available": True,
                    "reason": None,
                },
                {
                    "name": "pypdfium",
                    "file_types": ["pdf"],
                    "available": True,
                    "reason": None,
                },
            ],
        )

    parser = _parser_client(app, handler)
    app.dependency_overrides[get_parser_client] = lambda: parser
    try:
        response = await client.get("/api/v1/parse-engines")
    finally:
        app.dependency_overrides.pop(get_parser_client, None)
        await parser.aclose()

    assert response.status_code == 200, response.text
    by_name = {engine["name"]: engine for engine in response.json()}
    assert by_name["builtin"]["available"] is True
    assert by_name["builtin"]["file_types"] == ["txt", "csv", "excel"]
    assert by_name["pypdfium"]["available"] is True
    # 解析服务没注册的引擎标不可用，选项仍然齐全。
    assert by_name["docling"]["available"] is False
    assert by_name["docling"]["reason"] == "未配置"
    assert by_name["markitdown"]["available"] is False
    assert by_name["paddleocr"]["available"] is False

    tiers = {tier["tier"]: tier for tier in by_name["mineru"]["tiers"]}
    assert set(tiers) == {"flash", "basic", "standard", "advanced"}
    assert tiers["flash"]["available"] is False
    assert tiers["flash"]["label"] == "快速"
    assert tiers["basic"]["reason"] == "未配置"
    assert tiers["standard"]["available"] is False
    assert tiers["standard"]["reason"] == "未配置"
    assert tiers["advanced"]["reason"] == "未配置"
    assert MineruTier.flash.value == "flash"
