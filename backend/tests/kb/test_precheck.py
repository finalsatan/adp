"""上传预检：同名、内容重复、超长、类型与大小、默认方案解析（P3-07）。"""

from __future__ import annotations

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.kb.schemas import (
    ChunkAuto,
    ChunkWhole,
    FileType,
    ParseConfig,
    ParseEngine,
    SchemeConfig,
)
from app.kb.schemes import BUILTIN_SCHEMES
from app.kb.tables import Document, IngestScheme, KbDefaultScheme, KbFolder, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from app.settings import service as settings_service
from tests.factories import auth_cookies, make_org, make_user

PRE_CHECK_URL = "/api/v1/kbs/{kb_id}/documents:precheck"


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def seed_builtin_schemes(session: AsyncSession) -> None:
    """每个测试后 TRUNCATE 会清掉迁移写入的内置方案，测试前按固定 ID 重新写入。"""
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
    await session.commit()


async def make_kb(
    session: AsyncSession,
    *,
    org,
    user,
    name: str = "预检知识库",
    embedding_max_input: int = 8192,
) -> KnowledgeBase:
    resource = Resource(type="kb", name=name, org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    model = Model(
        name=f"向量模型-{name}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=1024,
        embedding_max_input=embedding_max_input,
        created_by=user.id,
    )
    session.add(model)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind="doc",
        embedding_model_id=model.id,
        embedding_dim=1024,
        index_table=f"kb_index.kb_{resource.id}",
    )
    session.add(kb)
    await session.commit()
    return kb


def _document(kb_id: int, user_id: int, folder_id: int | None, name: str, sha256: str) -> Document:

    return Document(
        kb_id=kb_id,
        folder_id=folder_id,
        name=name,
        file_type="pdf",
        ext="pdf",
        size=10,
        sha256=sha256,
        status="queued",
        uploaded_by=user_id,
        run_by=user_id,
    )


async def _make_folder(session: AsyncSession, kb_id: int, name: str) -> KbFolder:
    folder = KbFolder(kb_id=kb_id, parent_id=None, path="", name=name)
    session.add(folder)
    await session.flush()
    folder.path = f"/{folder.id}/"
    await session.commit()
    return folder


async def test_precheck_name_conflict_and_duplicate(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "预检组织")
    owner = await make_user(session, Role.builder, org, username="precheck_owner")
    kb = await make_kb(session, org=org, user=owner, name="预检库甲")
    other_kb = await make_kb(session, org=org, user=owner, name="预检库乙")
    await seed_builtin_schemes(session)

    folder = await _make_folder(session, kb.id, "甲")
    other_folder = await _make_folder(session, kb.id, "乙")
    content_sha = "a" * 64
    session.add(_document(kb.id, owner.id, folder.id, "同名.pdf", content_sha))
    await session.commit()

    login_as(client, owner)

    # 同文件夹同名 → name_conflict 有值。
    same_folder = await client.post(
        PRE_CHECK_URL.format(kb_id=kb.id),
        json={
            "files": [{"name": "同名.pdf", "size": 10, "sha256": "b" * 64}],
            "folder_id": folder.id,
        },
    )
    assert same_folder.status_code == 200, same_folder.text
    item = same_folder.json()["items"][0]
    assert item["name_conflict"]["name"] == "同名.pdf"
    assert item["duplicate"] is None

    # 不同文件夹同名 → 无冲突。
    other = await client.post(
        PRE_CHECK_URL.format(kb_id=kb.id),
        json={
            "files": [{"name": "同名.pdf", "size": 10, "sha256": "b" * 64}],
            "folder_id": other_folder.id,
        },
    )
    assert other.json()["items"][0]["name_conflict"] is None

    # 同库内容相同 → duplicate 有值。
    duplicate = await client.post(
        PRE_CHECK_URL.format(kb_id=kb.id),
        json={"files": [{"name": "别的内容.pdf", "size": 10, "sha256": content_sha}]},
    )
    assert duplicate.json()["items"][0]["duplicate"]["name"] == "同名.pdf"

    # 不同库相同内容 → 无。
    across = await client.post(
        PRE_CHECK_URL.format(kb_id=other_kb.id),
        json={"files": [{"name": "别的内容.pdf", "size": 10, "sha256": content_sha}]},
    )
    item = across.json()["items"][0]
    assert item["duplicate"] is None
    assert item["name_conflict"] is None


async def test_precheck_errors_for_type_and_size(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "预检错误组织")
    owner = await make_user(session, Role.builder, org, username="precheck_err_owner")
    kb = await make_kb(session, org=org, user=owner)
    await seed_builtin_schemes(session)
    login_as(client, owner)

    unsupported = await client.post(
        PRE_CHECK_URL.format(kb_id=kb.id),
        json={"files": [{"name": "木马.exe", "size": 10, "sha256": "a" * 64}]},
    )
    assert unsupported.status_code == 200
    assert unsupported.json()["errors"][0]["code"] == "UNSUPPORTED_FILE_TYPE"

    await settings_service.update_settings(session, {"upload": {"max_file_size_mb": 1}}, owner.id)
    too_large = await client.post(
        PRE_CHECK_URL.format(kb_id=kb.id),
        json={"files": [{"name": "大文件.pdf", "size": 2 * 1024 * 1024, "sha256": "a" * 64}]},
    )
    assert too_large.status_code == 200
    assert too_large.json()["errors"][0]["code"] == "FILE_TOO_LARGE"


async def test_precheck_too_long(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "超长组织")
    owner = await make_user(session, Role.builder, org, username="too_long_owner")
    # embedding_max_input=1000 → 上限 900。
    kb = await make_kb(session, org=org, user=owner, embedding_max_input=1000)
    await seed_builtin_schemes(session)

    def config(mode: str, max_tokens: int) -> dict:
        chunk = (
            ChunkWhole(max_tokens=max_tokens)
            if mode == "whole"
            else ChunkAuto(max_tokens=max_tokens)
        )
        return SchemeConfig(
            file_type=FileType.txt,
            parse=ParseConfig(engine=ParseEngine.builtin),
            chunk=chunk,
        ).model_dump(mode="json")

    long_scheme = IngestScheme(
        id=100,
        builtin=False,
        file_type="txt",
        name="超长方案",
        config=config("whole", 5000),
    )
    short_scheme = IngestScheme(
        id=101,
        builtin=False,
        file_type="txt",
        name="短方案",
        config=config("auto", 100),
    )
    session.add_all([long_scheme, short_scheme])
    await session.commit()
    login_as(client, owner)

    long_resp = await client.post(
        PRE_CHECK_URL.format(kb_id=kb.id),
        json={
            "files": [{"name": "a.txt", "size": 10, "sha256": "a" * 64}],
            "schemes": {"txt": long_scheme.id},
        },
    )
    long_item = long_resp.json()["items"][0]
    assert long_item["too_long"] is True
    assert long_item["estimated_tokens"] == 5000 + 200
    assert long_item["limit_tokens"] == 900

    short_resp = await client.post(
        PRE_CHECK_URL.format(kb_id=kb.id),
        json={
            "files": [{"name": "a.txt", "size": 10, "sha256": "a" * 64}],
            "schemes": {"txt": short_scheme.id},
        },
    )
    short_item = short_resp.json()["items"][0]
    assert short_item["too_long"] is False
    assert short_item["estimated_tokens"] == 300
    assert short_item["limit_tokens"] == 900


async def test_precheck_uses_kb_default_then_builtin(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "默认方案组织")
    owner = await make_user(session, Role.builder, org, username="default_scheme_owner")
    kb = await make_kb(session, org=org, user=owner)
    await seed_builtin_schemes(session)
    login_as(client, owner)

    # 没设默认：用该类型第一个内置方案（PDF → 1）。
    fallback = await client.post(
        PRE_CHECK_URL.format(kb_id=kb.id),
        json={"files": [{"name": "a.pdf", "size": 10, "sha256": "a" * 64}]},
    )
    assert fallback.json()["items"][0]["scheme_id"] == 1

    # 设了默认：用知识库默认方案（PDF 扫描件 → 2）。
    session.add(KbDefaultScheme(kb_id=kb.id, file_type="pdf", scheme_id=2))
    await session.commit()
    explicit = await client.post(
        PRE_CHECK_URL.format(kb_id=kb.id),
        json={"files": [{"name": "a.pdf", "size": 10, "sha256": "a" * 64}]},
    )
    assert explicit.json()["items"][0]["scheme_id"] == 2
    assert explicit.json()["items"][0]["scheme_name"] == "PDF · 扫描件"


async def test_precheck_permissions(client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "预检权限集团")
    rd = await make_org(session, "预检权限研发", parent=group)
    product = await make_org(session, "预检权限产品", parent=group)
    owner = await make_user(session, Role.builder, rd, username="precheck_perm_owner")
    viewer = await make_user(session, Role.builder, rd, username="precheck_perm_viewer")
    outsider = await make_user(session, Role.builder, product, username="precheck_perm_out")
    kb = await make_kb(session, org=rd, user=owner)
    await seed_builtin_schemes(session)

    payload = {"files": [{"name": "a.pdf", "size": 10, "sha256": "a" * 64}]}

    login_as(client, viewer)
    denied = await client.post(PRE_CHECK_URL.format(kb_id=kb.id), json=payload)
    assert denied.status_code == 403

    login_as(client, outsider)
    hidden = await client.post(PRE_CHECK_URL.format(kb_id=kb.id), json=payload)
    assert hidden.status_code == 404
