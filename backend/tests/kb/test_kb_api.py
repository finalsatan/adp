"""知识库管理接口测试（P3-04）：创建、列表、详情、编辑、停用启用、可见范围、协作者与权限。"""

import uuid

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user) -> None:
    """在 client 实例上设 Cookie，供后续请求使用。"""
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_embedding_model(
    session: AsyncSession,
    owner,
    *,
    dim: int = 1024,
    status: str = "enabled",
    model_type: str = "embedding",
) -> Model:
    name = f"模型-{uuid.uuid4().hex[:8]}"
    model = Model(
        name=name,
        model_type=model_type,
        litellm_model=(
            "openai/text-embedding-3-small" if model_type == "embedding" else "openai/gpt-4o-mini"
        ),
        embedding_dim=dim if model_type == "embedding" else None,
        embedding_max_input=8192 if model_type == "embedding" else None,
        status=status,
        created_by=owner.id,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def create_kb(
    client: AsyncClient,
    *,
    name: str,
    model_id: int,
    visibility: str = "org",
    kind: str = "doc",
    org_id: int | None = None,
):
    body: dict = {
        "kind": kind,
        "name": name,
        "embedding_model_id": model_id,
        "visibility": visibility,
    }
    if org_id is not None:
        body["org_id"] = org_id
    return await client.post("/api/v1/kbs", json=body)


async def audit_actions(session: AsyncSession, target_id: int) -> list[str]:
    rows = (
        (
            await session.execute(
                sa.select(AuditLog.action)
                .where(AuditLog.target_type == "kb", AuditLog.target_id == target_id)
                .order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def test_create_list_detail_doc_and_qa(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "知识库组织")
    builder = await make_user(session, Role.builder, org, username="kb_builder")
    model = await make_embedding_model(session, builder)
    login_as(client, builder)

    doc_resp = await create_kb(client, name="文档库A", model_id=model.id, kind="doc")
    assert doc_resp.status_code == 201, doc_resp.text
    doc = doc_resp.json()
    assert doc["kind"] == "doc"
    assert doc["doc_count"] == 0 and doc["chunk_count"] == 0
    assert doc["embedding_model"] == {
        "id": model.id,
        "name": model.name,
        "embedding_dim": 1024,
        "embedding_max_input": 8192,
        "status": "enabled",
    }
    assert doc["org"]["id"] == org.id
    assert doc["owner"]["id"] == builder.id
    assert doc["visibility"] == "org"
    assert doc["status"] == "enabled"
    assert doc["editable"] is True and doc["manageable"] is True

    qa_resp = await create_kb(client, name="问答库B", model_id=model.id, kind="qa")
    assert qa_resp.status_code == 201, qa_resp.text
    qa = qa_resp.json()
    assert qa["kind"] == "qa"
    # 问答库第三期只建库，没有问答对，计数为 0。
    assert qa["doc_count"] == 0 and qa["chunk_count"] == 0

    kb_row = await session.get(KnowledgeBase, doc["id"])
    assert kb_row is not None
    assert kb_row.index_table == f"kb_index.kb_{doc['id']}"
    assert kb_row.index_schema_version == 1
    assert kb_row.retrieval_config == {}

    listing = await client.get("/api/v1/kbs")
    assert listing.status_code == 200
    assert listing.json()["total"] == 2
    assert {item["id"] for item in listing.json()["items"]} == {doc["id"], qa["id"]}

    detail = await client.get(f"/api/v1/kbs/{doc['id']}")
    assert detail.status_code == 200
    assert detail.json()["name"] == "文档库A"
    assert detail.json()["kind"] == "doc"

    # 创建写 kb.create 审计
    assert "kb.create" in await audit_actions(session, doc["id"])


async def test_embedding_model_unavailable(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "模型校验组织")
    builder = await make_user(session, Role.builder, org, username="model_builder")
    disabled = await make_embedding_model(session, builder, status="disabled")
    chat = await make_embedding_model(session, builder, model_type="chat")
    deleted = await make_embedding_model(session, builder)
    deleted.deleted_at = sa.func.now()
    await session.commit()
    login_as(client, builder)

    missing = await create_kb(client, name="缺模型", model_id=999999)
    assert missing.status_code == 409
    assert missing.json()["code"] == "EMBEDDING_MODEL_UNAVAILABLE"

    for index, model in enumerate((disabled, chat, deleted)):
        response = await create_kb(client, name=f"不可用{index}", model_id=model.id)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "EMBEDDING_MODEL_UNAVAILABLE"


async def test_duplicate_name_same_org_and_org_required(
    client: AsyncClient, session: AsyncSession
) -> None:
    org_a = await make_org(session, "重名组织A")
    org_b = await make_org(session, "重名组织B")
    admin = await make_user(session, Role.sys_admin, username="dup_admin")
    model = await make_embedding_model(session, admin)
    login_as(client, admin)

    # 系统管理员必须给 org_id
    missing_org = await create_kb(client, name="没有组织", model_id=model.id)
    assert missing_org.status_code == 422
    assert missing_org.json()["code"] == "ORG_REQUIRED"

    first = await create_kb(client, name="同名库", model_id=model.id, org_id=org_a.id)
    assert first.status_code == 201
    duplicate = await create_kb(client, name="同名库", model_id=model.id, org_id=org_a.id)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "NAME_CONFLICT"
    other_org = await create_kb(client, name="同名库", model_id=model.id, org_id=org_b.id)
    assert other_org.status_code == 201


async def test_update_immutable_fields_and_audit(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "编辑组织")
    builder = await make_user(session, Role.builder, org, username="edit_builder")
    model = await make_embedding_model(session, builder)
    login_as(client, builder)
    kb = (await create_kb(client, name="旧名称", model_id=model.id)).json()

    kind = await client.patch(f"/api/v1/kbs/{kb['id']}", json={"kind": "qa"})
    assert kind.status_code == 422
    assert kind.json()["code"] == "KB_KIND_IMMUTABLE"

    model_change = await client.patch(
        f"/api/v1/kbs/{kb['id']}", json={"embedding_model_id": model.id}
    )
    assert model_change.status_code == 422
    assert model_change.json()["code"] == "KB_EMBEDDING_MODEL_IMMUTABLE"

    renamed = await client.patch(
        f"/api/v1/kbs/{kb['id']}", json={"name": "新名称", "description": "新描述"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "新名称"
    assert renamed.json()["description"] == "新描述"

    logs = (
        (
            await session.execute(
                sa.select(AuditLog).where(
                    AuditLog.target_type == "kb", AuditLog.target_id == kb["id"]
                )
            )
        )
        .scalars()
        .all()
    )
    update_log = next(log for log in logs if log.action == "kb.update")
    assert update_log.target_desc == "新名称"
    assert update_log.target_org_id == org.id

    # 同组织内改名冲突
    await create_kb(client, name="占用的名字", model_id=model.id)
    conflict = await client.patch(f"/api/v1/kbs/{kb['id']}", json={"name": "占用的名字"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "NAME_CONFLICT"


async def test_disable_enable_audit(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "启停组织")
    builder = await make_user(session, Role.builder, org, username="status_builder")
    model = await make_embedding_model(session, builder)
    login_as(client, builder)
    kb = (await create_kb(client, name="启停库", model_id=model.id)).json()

    disabled = await client.post(f"/api/v1/kbs/{kb['id']}:disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    enabled = await client.post(f"/api/v1/kbs/{kb['id']}:enable")
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "enabled"

    actions = await audit_actions(session, kb["id"])
    assert "kb.disable" in actions and "kb.enable" in actions


async def test_visibility_members_and_audit(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "协作者组织")
    owner = await make_user(session, Role.builder, org, username="collab_owner")
    member = await make_user(session, Role.builder, org, username="collab_member")
    model = await make_embedding_model(session, owner)
    login_as(client, owner)
    kb = (await create_kb(client, name="协作者库", model_id=model.id, visibility="private")).json()

    added = await client.post(f"/api/v1/kbs/{kb['id']}/members", json={"user_id": member.id})
    assert added.status_code == 201, added.text
    assert added.json()["user"]["id"] == member.id

    listing = await client.get(f"/api/v1/kbs/{kb['id']}/members")
    assert listing.status_code == 200
    assert [row["user"]["id"] for row in listing.json()] == [member.id]

    changed = await client.patch(f"/api/v1/kbs/{kb['id']}/visibility", json={"visibility": "org"})
    assert changed.status_code == 200
    assert changed.json()["visibility"] == "org"

    removed = await client.delete(f"/api/v1/kbs/{kb['id']}/members/{member.id}")
    assert removed.status_code == 204

    logs = (
        (
            await session.execute(
                sa.select(AuditLog)
                .where(AuditLog.target_type == "kb", AuditLog.target_id == kb["id"])
                .order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )
    actions = {log.action for log in logs}
    assert {"kb.add_member", "kb.change_visibility", "kb.remove_member"} <= actions
    visibility_log = next(log for log in logs if log.action == "kb.change_visibility")
    assert visibility_log.detail == {"from": "private", "to": "org"}
    add_log = next(log for log in logs if log.action == "kb.add_member")
    assert add_log.detail == {"username": member.username}


async def test_view_edit_manage_permissions(client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "权限集团")
    rd = await make_org(session, "权限研发", parent=group)
    product = await make_org(session, "权限产品", parent=group)
    owner = await make_user(session, Role.builder, rd, username="perm_owner")
    viewer = await make_user(session, Role.builder, rd, username="perm_viewer")
    outsider = await make_user(session, Role.builder, product, username="perm_outsider")
    model = await make_embedding_model(session, owner)

    login_as(client, owner)
    kb = (await create_kb(client, name="权限库", model_id=model.id, visibility="org")).json()

    # 能看不能管：查看 200，管理类操作 403。
    login_as(client, viewer)
    assert (await client.get("/api/v1/kbs")).json()["total"] == 1
    assert (await client.get(f"/api/v1/kbs/{kb['id']}")).status_code == 200
    assert (await client.get(f"/api/v1/kbs/{kb['id']}/refs")).status_code == 200
    assert (await client.get(f"/api/v1/kbs/{kb['id']}/members")).status_code == 200
    for method, path, kwargs in (
        ("patch", f"/api/v1/kbs/{kb['id']}", {"json": {"name": "改名"}}),
        ("post", f"/api/v1/kbs/{kb['id']}:disable", {}),
        ("post", f"/api/v1/kbs/{kb['id']}:enable", {}),
        ("patch", f"/api/v1/kbs/{kb['id']}/visibility", {"json": {"visibility": "platform"}}),
        ("post", f"/api/v1/kbs/{kb['id']}/members", {"json": {"user_id": owner.id}}),
        ("delete", f"/api/v1/kbs/{kb['id']}/members/{owner.id}", {}),
        ("delete", f"/api/v1/kbs/{kb['id']}", {}),
    ):
        response = await getattr(client, method)(path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)

    # 看不到：详情与操作都 404（不暴露是否存在）。
    login_as(client, outsider)
    assert (await client.get(f"/api/v1/kbs/{kb['id']}")).status_code == 404
    assert (await client.post(f"/api/v1/kbs/{kb['id']}:disable")).status_code == 404
    assert (await client.get(f"/api/v1/kbs/{kb['id']}/members")).status_code == 404


async def test_end_user_forbidden(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "终端用户组织")
    end_user = await make_user(session, Role.end_user, org, username="end_user_kb")
    owner = await make_user(session, Role.builder, org, username="end_user_owner")
    model = await make_embedding_model(session, owner)
    login_as(client, owner)
    kb = (await create_kb(client, name="终端库", model_id=model.id)).json()

    login_as(client, end_user)
    assert (await client.get("/api/v1/kbs")).status_code == 403
    assert (await client.get(f"/api/v1/kbs/{kb['id']}")).status_code == 403
    create = await create_kb(client, name="终端建库", model_id=model.id)
    assert create.status_code == 403
    assert create.json()["code"] == "PERMISSION_DENIED"


async def test_refs_empty(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "引用组织")
    builder = await make_user(session, Role.builder, org, username="refs_builder")
    model = await make_embedding_model(session, builder)
    login_as(client, builder)
    kb = (await create_kb(client, name="引用库", model_id=model.id)).json()

    refs = await client.get(f"/api/v1/kbs/{kb['id']}/refs")
    assert refs.status_code == 200
    assert refs.json() == []


async def test_list_filters_and_keyword_escape(client: AsyncClient, session: AsyncSession) -> None:
    group = await make_org(session, "筛选集团")
    rd = await make_org(session, "筛选研发", parent=group)
    product = await make_org(session, "筛选产品", parent=group)
    admin = await make_user(session, Role.sys_admin, username="filter_admin")
    model = await make_embedding_model(session, admin)
    login_as(client, admin)

    await create_kb(client, name="员工制度", model_id=model.id, org_id=rd.id, visibility="org")
    qa = await create_kb(
        client,
        name="常见问题",
        model_id=model.id,
        org_id=product.id,
        visibility="platform",
        kind="qa",
    )
    assert qa.status_code == 201
    await client.post(f"/api/v1/kbs/{qa.json()['id']}:disable")

    by_kind = await client.get("/api/v1/kbs", params={"kind": "qa"})
    assert by_kind.json()["total"] == 1
    assert by_kind.json()["items"][0]["id"] == qa.json()["id"]

    by_visibility = await client.get("/api/v1/kbs", params={"visibility": "org"})
    assert by_visibility.json()["total"] == 1

    by_status = await client.get("/api/v1/kbs", params={"status": "disabled"})
    assert by_status.json()["total"] == 1

    # 含下级：按集团筛选能查到子组织；不含下级查不到。
    with_sub = await client.get("/api/v1/kbs", params={"org_id": group.id, "include_sub": True})
    assert with_sub.json()["total"] == 2
    without_sub = await client.get("/api/v1/kbs", params={"org_id": group.id, "include_sub": False})
    assert without_sub.json()["total"] == 0

    # LIKE 通配符要转义：输入 % 不应匹配全部。
    wildcard = await client.get("/api/v1/kbs", params={"keyword": "%"})
    assert wildcard.json()["total"] == 0
    matched = await client.get("/api/v1/kbs", params={"keyword": "员工"})
    assert matched.json()["total"] == 1
    assert matched.json()["items"][0]["name"] == "员工制度"


async def test_index_dimension_matches_model(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "维度组织")
    builder = await make_user(session, Role.builder, org, username="dim_builder")
    model_1024 = await make_embedding_model(session, builder, dim=1024)
    model_2560 = await make_embedding_model(session, builder, dim=2560)
    login_as(client, builder)

    kb_1024 = (await create_kb(client, name="小维度", model_id=model_1024.id)).json()
    kb_2560 = (await create_kb(client, name="大维度", model_id=model_2560.id)).json()

    async def embedding_type(kb_id: int) -> str | None:
        return await session.scalar(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = to_regclass(:t) AND attname = 'embedding'"
            ),
            {"t": f"kb_index.kb_{kb_id}"},
        )

    assert await embedding_type(kb_1024["id"]) == "vector(1024)"
    assert await embedding_type(kb_2560["id"]) == "halfvec(2560)"
