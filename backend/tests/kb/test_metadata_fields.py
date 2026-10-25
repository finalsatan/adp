"""知识库元数据字段接口与取值校验（P3-05）。"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import MetadataValueInvalidError
from app.core.schemas import Role
from app.iam.context import Ctx
from app.iam.permissions import ROLE_PERMISSIONS
from app.iam.tables import Org
from app.kb import metadata
from app.kb.schemas import MetadataFieldType
from app.kb.tables import Document, KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def ctx_of(session: AsyncSession, user) -> Ctx:
    org = await session.get(Org, user.org_id) if user.org_id is not None else None
    role = Role(user.role)
    return Ctx(
        user_id=user.id,
        username=user.username,
        name=user.name,
        role=role,
        status=user.status,
        org_id=org.id if org is not None else None,
        org_path=org.path if org is not None else None,
        org_name=org.name if org is not None else None,
        perms=ROLE_PERMISSIONS.get(role, frozenset()),
        session_id="test-session",
        pwc=False,
        ip=None,
        request_id=None,
    )


async def make_kb(
    session: AsyncSession, *, org: Org, user, name: str = "测试知识库", dim: int = 1024
) -> KnowledgeBase:
    resource = Resource(type="kb", name=name, org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    model = Model(
        name=f"向量模型-{name}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=dim,
        created_by=user.id,
    )
    session.add(model)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind="doc",
        embedding_model_id=model.id,
        embedding_dim=dim,
        index_table=f"kb_index.kb_{resource.id}",
    )
    session.add(kb)
    await session.commit()
    return kb


def _document(kb_id: int, user_id: int, name: str, metadata_value: dict | None = None) -> Document:
    return Document(
        kb_id=kb_id,
        folder_id=None,
        name=name,
        file_type="pdf",
        ext="pdf",
        size=1024,
        sha256="a" * 64,
        status="queued",
        doc_metadata=metadata_value or {},
        uploaded_by=user_id,
        run_by=user_id,
    )


async def test_create_four_types_and_conflicts(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "字段组织")
    owner = await make_user(session, Role.builder, org, username="field_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    for name, field_type in (("文本", "text"), ("数字", "number"), ("日期", "date")):
        response = await client.post(
            f"/api/v1/kbs/{kb.id}/metadata-fields", json={"name": name, "type": field_type}
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["type"] == field_type
        assert body["options"] is None
        assert body["doc_count"] == 0

    selected = await client.post(
        f"/api/v1/kbs/{kb.id}/metadata-fields",
        json={"name": "单选", "type": "select", "options": ["全国", "华北", "华北", " 华东 "]},
    )
    assert selected.status_code == 201, selected.text
    # 选项去空白、去重。
    assert selected.json()["options"] == ["全国", "华北", "华东"]

    # 单选不给选项 422。
    missing = await client.post(
        f"/api/v1/kbs/{kb.id}/metadata-fields", json={"name": "无选项", "type": "select"}
    )
    assert missing.status_code == 422

    # 知识库内重名 409。
    duplicate = await client.post(
        f"/api/v1/kbs/{kb.id}/metadata-fields", json={"name": "文本", "type": "text"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "NAME_CONFLICT"


async def test_metadata_doc_count(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "计数组织")
    owner = await make_user(session, Role.builder, org, username="count_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    field = (
        await client.post(
            f"/api/v1/kbs/{kb.id}/metadata-fields", json={"name": "部门", "type": "text"}
        )
    ).json()
    session.add_all(
        [
            _document(kb.id, owner.id, "a.pdf", {"部门": "研发"}),
            _document(kb.id, owner.id, "b.pdf", {"部门": "财务"}),
            _document(kb.id, owner.id, "c.pdf", {}),
        ]
    )
    await session.commit()

    listing = await client.get(f"/api/v1/kbs/{kb.id}/metadata-fields")
    assert listing.status_code == 200
    items = {item["id"]: item for item in listing.json()}
    assert items[field["id"]]["doc_count"] == 2


async def test_update_field_name_and_options(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "编辑组织")
    owner = await make_user(session, Role.builder, org, username="field_edit_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    field = (
        await client.post(
            f"/api/v1/kbs/{kb.id}/metadata-fields",
            json={"name": "单选", "type": "select", "options": ["A", "B"]},
        )
    ).json()
    updated = await client.patch(
        f"/api/v1/metadata-fields/{field['id']}",
        json={"name": "新单选", "options": ["A", "C"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "新单选"
    assert updated.json()["options"] == ["A", "C"]
    assert updated.json()["type"] == "select"

    other = (
        await client.post(
            f"/api/v1/kbs/{kb.id}/metadata-fields", json={"name": "另一个", "type": "text"}
        )
    ).json()
    conflict = await client.patch(f"/api/v1/metadata-fields/{other['id']}", json={"name": "新单选"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "NAME_CONFLICT"


async def test_validate_metadata(session: AsyncSession) -> None:
    org = await make_org(session, "校验组织")
    owner = await make_user(session, Role.builder, org, username="validate_owner")
    kb = await make_kb(session, org=org, user=owner)
    ctx = await ctx_of(session, owner)

    await metadata.create_field(session, ctx, kb.id, "文本", MetadataFieldType.text, None)
    await metadata.create_field(session, ctx, kb.id, "数字", MetadataFieldType.number, None)
    await metadata.create_field(session, ctx, kb.id, "日期", MetadataFieldType.date, None)
    await metadata.create_field(session, ctx, kb.id, "单选", MetadataFieldType.select, ["A", "B"])

    # 合法值通过；空字典直接通过。
    await metadata.validate_metadata(
        session,
        kb.id,
        {"文本": "说明", "数字": 3.5, "日期": "2024-01-02", "单选": "A"},
    )
    await metadata.validate_metadata(session, kb.id, {})

    for bad in (
        {"数字": "3"},
        {"数字": True},
        {"单选": "C"},
        {"未定义": "x"},
        {"文本": 3},
        {"日期": "2024/01/02"},
        {"日期": "2024-13-40"},
    ):
        with pytest.raises(MetadataValueInvalidError) as exc:
            await metadata.validate_metadata(session, kb.id, bad)
        assert exc.value.status == 422
        assert exc.value.code == "METADATA_VALUE_INVALID"
        assert exc.value.detail is not None
        assert "field" in exc.value.detail
