"""知识库文件夹接口、物化路径与含下级过滤（P3-05）。"""

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.iam.context import Ctx
from app.iam.permissions import ROLE_PERMISSIONS
from app.iam.tables import Org
from app.kb import folders
from app.kb.tables import Document, KbFolder, KnowledgeBase
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


def _document(
    kb_id: int, user_id: int, folder_id: int | None, name: str, metadata: dict | None = None
) -> Document:
    return Document(
        kb_id=kb_id,
        folder_id=folder_id,
        name=name,
        file_type="pdf",
        ext="pdf",
        size=1024,
        sha256="a" * 64,
        status="queued",
        doc_metadata=metadata or {},
        uploaded_by=user_id,
        run_by=user_id,
    )


async def test_create_nested_folders_and_list(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "文件夹组织")
    owner = await make_user(session, Role.builder, org, username="folder_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    parent_resp = await client.post(f"/api/v1/kbs/{kb.id}/folders", json={"name": "甲"})
    assert parent_resp.status_code == 201, parent_resp.text
    parent = parent_resp.json()
    assert parent["path"] == f"/{parent['id']}/"
    assert parent["parent_id"] is None
    assert parent["kb_id"] == kb.id
    assert parent["doc_count"] == 0

    child_resp = await client.post(
        f"/api/v1/kbs/{kb.id}/folders", json={"name": "乙", "parent_id": parent["id"]}
    )
    assert child_resp.status_code == 201, child_resp.text
    child = child_resp.json()
    assert child["path"] == f"/{parent['id']}/{child['id']}/"

    third_resp = await client.post(f"/api/v1/kbs/{kb.id}/folders", json={"name": "丙"})
    third = third_resp.json()

    listing = await client.get(f"/api/v1/kbs/{kb.id}/folders")
    assert listing.status_code == 200
    items = listing.json()
    assert {item["id"] for item in items} == {parent["id"], child["id"], third["id"]}
    paths = [item["path"] for item in items]
    assert paths == sorted(paths)

    # 同级重名 409；不同级可以同名。
    dup = await client.post(f"/api/v1/kbs/{kb.id}/folders", json={"name": "甲"})
    assert dup.status_code == 409
    assert dup.json()["code"] == "NAME_CONFLICT"
    same = await client.post(
        f"/api/v1/kbs/{kb.id}/folders", json={"name": "甲", "parent_id": parent["id"]}
    )
    assert same.status_code == 201

    # 父文件夹属于别的知识库 → 404。
    other = await make_kb(session, org=org, user=owner, name="另一个库")
    other_folder = (
        await client.post(f"/api/v1/kbs/{other.id}/folders", json={"name": "外库"})
    ).json()
    bad = await client.post(
        f"/api/v1/kbs/{kb.id}/folders",
        json={"name": "子", "parent_id": other_folder["id"]},
    )
    assert bad.status_code == 404


async def test_folder_scope_clause_includes_descendants(session: AsyncSession) -> None:
    org = await make_org(session, "范围组织")
    owner = await make_user(session, Role.builder, org, username="scope_owner")
    kb = await make_kb(session, org=org, user=owner)
    ctx = await ctx_of(session, owner)

    parent = await folders.create_folder(session, ctx, kb.id, None, "父")
    child = await folders.create_folder(session, ctx, kb.id, parent.id, "子")
    sibling = await folders.create_folder(session, ctx, kb.id, None, "旁支")

    session.add_all(
        [
            _document(kb.id, owner.id, parent.id, "父内.pdf"),
            _document(kb.id, owner.id, child.id, "子内.pdf"),
            _document(kb.id, owner.id, sibling.id, "旁支内.pdf"),
            _document(kb.id, owner.id, None, "未分类.pdf"),
        ]
    )
    await session.commit()

    parent_row = await session.get(KbFolder, parent.id)
    assert parent_row is not None
    names = set(
        (
            await session.execute(
                sa.select(Document.name)
                .join(KbFolder, Document.folder_id == KbFolder.id)
                .where(Document.kb_id == kb.id, folders.folder_scope_clause(parent_row))
            )
        )
        .scalars()
        .all()
    )
    assert names == {"父内.pdf", "子内.pdf"}


async def test_delete_folder_conflicts_and_success(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "删除组织")
    owner = await make_user(session, Role.builder, org, username="del_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    parent = (await client.post(f"/api/v1/kbs/{kb.id}/folders", json={"name": "父"})).json()
    child = (
        await client.post(
            f"/api/v1/kbs/{kb.id}/folders", json={"name": "子", "parent_id": parent["id"]}
        )
    ).json()

    has_children = await client.delete(f"/api/v1/folders/{parent['id']}")
    assert has_children.status_code == 409
    assert has_children.json()["code"] == "FOLDER_HAS_CHILDREN"

    session.add(_document(kb.id, owner.id, child["id"], "有文档.pdf"))
    await session.commit()
    not_empty = await client.delete(f"/api/v1/folders/{child['id']}")
    assert not_empty.status_code == 409
    assert not_empty.json()["code"] == "FOLDER_NOT_EMPTY"

    empty = (await client.post(f"/api/v1/kbs/{kb.id}/folders", json={"name": "空"})).json()
    deleted = await client.delete(f"/api/v1/folders/{empty['id']}")
    assert deleted.status_code == 204
    listing = (await client.get(f"/api/v1/kbs/{kb.id}/folders")).json()
    assert empty["id"] not in {item["id"] for item in listing}


async def test_delete_metadata_field_removes_value(
    client: AsyncClient, session: AsyncSession
) -> None:
    """删除元数据字段时，同一事务里从文档 metadata 删掉该键（卡片列在 test_folders）。"""
    org = await make_org(session, "元数据删除组织")
    owner = await make_user(session, Role.builder, org, username="field_del_owner")
    kb = await make_kb(session, org=org, user=owner)
    login_as(client, owner)

    field = (
        await client.post(
            f"/api/v1/kbs/{kb.id}/metadata-fields", json={"name": "部门", "type": "text"}
        )
    ).json()
    session.add_all(
        [
            _document(kb.id, owner.id, None, "a.pdf", {"部门": "研发", "编号": "1"}),
            _document(kb.id, owner.id, None, "b.pdf", {"编号": "2"}),
        ]
    )
    await session.commit()

    deleted = await client.delete(f"/api/v1/metadata-fields/{field['id']}")
    assert deleted.status_code == 204
    metas = (
        (await session.execute(sa.select(Document.doc_metadata).where(Document.kb_id == kb.id)))
        .scalars()
        .all()
    )
    assert all("部门" not in meta for meta in metas)
    assert all(meta.get("编号") for meta in metas)


async def test_folder_permissions(client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "权限集团")
    rd = await make_org(session, "权限研发", parent=group)
    product = await make_org(session, "权限产品", parent=group)
    owner = await make_user(session, Role.builder, rd, username="perm_owner")
    viewer = await make_user(session, Role.builder, rd, username="perm_viewer")
    outsider = await make_user(session, Role.builder, product, username="perm_outsider")
    kb = await make_kb(session, org=rd, user=owner)

    login_as(client, owner)
    folder = (await client.post(f"/api/v1/kbs/{kb.id}/folders", json={"name": "甲"})).json()

    # 能看不能编辑：列表 200，增删改 403。
    login_as(client, viewer)
    assert (await client.get(f"/api/v1/kbs/{kb.id}/folders")).status_code == 200
    create = await client.post(f"/api/v1/kbs/{kb.id}/folders", json={"name": "乙"})
    assert create.status_code == 403
    rename = await client.patch(f"/api/v1/folders/{folder['id']}", json={"name": "丙"})
    assert rename.status_code == 403
    assert (await client.delete(f"/api/v1/folders/{folder['id']}")).status_code == 403

    # 看不到知识库：404。
    login_as(client, outsider)
    assert (await client.get(f"/api/v1/kbs/{kb.id}/folders")).status_code == 404
    hidden = await client.post(f"/api/v1/kbs/{kb.id}/folders", json={"name": "乙"})
    assert hidden.status_code == 404
