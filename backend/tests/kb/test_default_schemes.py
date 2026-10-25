"""知识库按文件类型的默认入库方案（P3-06）。"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.kb.schemas import FileType
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user
from tests.kb.test_schemes import seed_builtin_schemes


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


@pytest_asyncio.fixture(autouse=True)
async def _builtin_schemes(session: AsyncSession) -> None:
    await seed_builtin_schemes(session)


async def make_kb(session: AsyncSession, *, org, user, name: str = "默认方案库") -> KnowledgeBase:
    resource = Resource(type="kb", name=name, org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    model = Model(
        name=f"向量模型-{uuid.uuid4().hex[:8]}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=1024,
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


def _item(body: dict, file_type: str) -> dict:
    return next(item for item in body["items"] if item["file_type"] == file_type)


async def test_default_schemes_fallback(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "默认组织")
    owner = await make_user(session, Role.builder, org, username="default_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    response = await client.get(f"/api/v1/kbs/{kb.id}/default-schemes")
    assert response.status_code == 200, response.text
    body = response.json()
    # 全部 11 种文件类型都返回，未设置时只有 fallback。
    assert [item["file_type"] for item in body["items"]] == [ft.value for ft in FileType]
    assert all(item["scheme_id"] is None for item in body["items"])
    assert all(item["scheme_name"] is None for item in body["items"])
    assert _item(body, "pdf")["fallback_scheme_id"] == 1
    assert _item(body, "pdf")["fallback_scheme_name"] == "PDF · 通用"
    assert _item(body, "word")["fallback_scheme_id"] == 3
    assert _item(body, "image")["fallback_scheme_id"] == 12
    assert all(item["fallback_scheme_id"] is not None for item in body["items"])


async def test_set_and_clear_default_schemes(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "设置组织")
    owner = await make_user(session, Role.builder, org, username="set_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    saved = await client.put(
        f"/api/v1/kbs/{kb.id}/default-schemes",
        json={"items": [{"file_type": "pdf", "scheme_id": 2}]},
    )
    assert saved.status_code == 200, saved.text
    pdf = _item(saved.json(), "pdf")
    assert pdf["scheme_id"] == 2
    assert pdf["scheme_name"] == "PDF · 扫描件"
    assert pdf["fallback_scheme_id"] == 1
    # 未提交的类型不动。
    assert _item(saved.json(), "word")["scheme_id"] is None

    # 重新 GET 仍返回设置值。
    listed = await client.get(f"/api/v1/kbs/{kb.id}/default-schemes")
    assert _item(listed.json(), "pdf")["scheme_id"] == 2

    # 清除：scheme_id 为 null 回到 fallback。
    cleared = await client.put(
        f"/api/v1/kbs/{kb.id}/default-schemes",
        json={"items": [{"file_type": "pdf", "scheme_id": None}]},
    )
    assert cleared.status_code == 200, cleared.text
    pdf = _item(cleared.json(), "pdf")
    assert pdf["scheme_id"] is None
    assert pdf["scheme_name"] is None
    assert pdf["fallback_scheme_id"] == 1


async def test_default_scheme_type_mismatch(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "不符组织")
    owner = await make_user(session, Role.builder, org, username="mismatch_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    # 1 号是 PDF 方案，放到 word 上：422 SCHEME_TYPE_MISMATCH。
    response = await client.put(
        f"/api/v1/kbs/{kb.id}/default-schemes",
        json={"items": [{"file_type": "word", "scheme_id": 1}]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "SCHEME_TYPE_MISMATCH"

    missing = await client.put(
        f"/api/v1/kbs/{kb.id}/default-schemes",
        json={"items": [{"file_type": "pdf", "scheme_id": 9999}]},
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "SCHEME_UNAVAILABLE"


async def test_default_scheme_permissions(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "权限组织")
    other = await make_org(session, "别的组织")
    owner = await make_user(session, Role.builder, org, username="perm_default_owner")
    viewer = await make_user(session, Role.builder, org, username="perm_default_viewer")
    outsider = await make_user(session, Role.builder, other, username="perm_default_outsider")
    kb = await make_kb(session, org=org, user=owner)

    login_as(client, viewer)
    assert (await client.get(f"/api/v1/kbs/{kb.id}/default-schemes")).status_code == 200
    denied = await client.put(
        f"/api/v1/kbs/{kb.id}/default-schemes",
        json={"items": [{"file_type": "pdf", "scheme_id": 1}]},
    )
    assert denied.status_code == 403, denied.text

    login_as(client, outsider)
    assert (await client.get(f"/api/v1/kbs/{kb.id}/default-schemes")).status_code == 404
    assert (
        await client.put(
            f"/api/v1/kbs/{kb.id}/default-schemes",
            json={"items": [{"file_type": "pdf", "scheme_id": 1}]},
        )
    ).status_code == 404
