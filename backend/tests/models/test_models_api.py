"""模型管理接口：权限、校验、字段锁定、引用、删除与审计。"""

import json
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.settings import service as settings_service
from tests.factories import auth_cookies, make_org, make_user

CHAT_MODEL: dict[str, Any] = {
    "name": "测试对话模型",
    "model_type": "chat",
    "litellm_model": "openai/gpt-4o",
    "context_length": 128000,
    "max_output": 4096,
}

EMBEDDING_MODEL: dict[str, Any] = {
    "name": "测试向量模型",
    "model_type": "embedding",
    "litellm_model": "dashscope/text-embedding-v3",
    "embedding_dim": 1024,
    "embedding_max_input": 8192,
    "embedding_batch_size": 10,
}


@pytest_asyncio.fixture
async def admin(session: AsyncSession):
    return await make_user(session, role=Role.sys_admin)


@pytest_asyncio.fixture
async def admin_client(client: AsyncClient, admin) -> AsyncClient:
    client.cookies.update(auth_cookies(admin))
    return client


async def _login(client: AsyncClient, user) -> None:
    client.cookies.update(auth_cookies(user))


async def _create_model(client: AsyncClient, body: dict[str, Any] | None = None) -> dict:
    response = await client.post("/api/v1/models", json=body or CHAT_MODEL)
    assert response.status_code == 201, response.text
    return response.json()


async def _add_kb_ref(session: AsyncSession, model_id: int) -> int:
    """造一条“知识库引用了该模型”的 resource_refs。返回资源 id。"""
    org = await make_org(session, "知识库组织")
    owner = await make_user(session, role=Role.builder, org=org)
    resource_id = await session.scalar(
        text(
            "INSERT INTO resources (type, name, org_id, owner_id, visibility) "
            "VALUES ('kb', '测试知识库', :org_id, :owner_id, 'org') RETURNING id"
        ),
        {"org_id": org.id, "owner_id": owner.id},
    )
    await session.execute(
        text(
            "INSERT INTO resource_refs (target_type, target_id, ref_resource_id) "
            "VALUES ('model', :model_id, :resource_id)"
        ),
        {"model_id": model_id, "resource_id": resource_id},
    )
    await session.commit()
    return resource_id


@pytest.mark.parametrize("role", [Role.builder, Role.org_admin])
async def test_non_admin_can_view_but_not_manage(
    client: AsyncClient, session: AsyncSession, org_tree, admin, role: Role
) -> None:
    await _login(client, admin)
    model = await _create_model(client)

    user = await make_user(session, role=role, org=org_tree.rd)
    await _login(client, user)

    assert (await client.get("/api/v1/models")).status_code == 200
    assert (await client.get(f"/api/v1/models/{model['id']}")).status_code == 200

    for method, path, body in [
        ("POST", "/api/v1/models", CHAT_MODEL),
        ("PATCH", f"/api/v1/models/{model['id']}", {"name": "改名"}),
        ("POST", f"/api/v1/models/{model['id']}:disable", None),
        ("DELETE", f"/api/v1/models/{model['id']}", None),
    ]:
        response = await client.request(method, path, json=body)
        assert response.status_code == 403, f"{method} {path}: {response.text}"
        assert response.json()["code"] == "PERMISSION_DENIED"


async def test_name_conflict_and_reuse_after_delete(
    admin_client: AsyncClient,
) -> None:
    model = await _create_model(admin_client)

    conflict = await admin_client.post("/api/v1/models", json=CHAT_MODEL)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "NAME_CONFLICT"

    assert (await admin_client.delete(f"/api/v1/models/{model['id']}")).status_code == 204
    assert (await admin_client.get("/api/v1/models")).json()["total"] == 0

    # 软删除后名称可以再用。
    await _create_model(admin_client)


async def test_embedding_dim_out_of_range(admin_client: AsyncClient) -> None:
    response = await admin_client.post(
        "/api/v1/models", json={**EMBEDDING_MODEL, "embedding_dim": 4001}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_chat_missing_context_length(admin_client: AsyncClient) -> None:
    body = {key: value for key, value in CHAT_MODEL.items() if key != "context_length"}
    response = await admin_client.post("/api/v1/models", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_model_host_not_allowed_then_allowlisted(
    admin_client: AsyncClient, session: AsyncSession, admin
) -> None:
    body = {**CHAT_MODEL, "api_base": "http://127.0.0.1:8000"}
    denied = await admin_client.post("/api/v1/models", json=body)
    assert denied.status_code == 422
    assert denied.json()["code"] == "MODEL_HOST_NOT_ALLOWED"

    await settings_service.update_settings(
        session, {"model_call": {"model_host_allowlist": ["127.0.0.1"]}}, actor_id=admin.id
    )
    created = await admin_client.post("/api/v1/models", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["api_base"] == "http://127.0.0.1:8000"


async def test_model_type_immutable(admin_client: AsyncClient) -> None:
    model = await _create_model(admin_client)
    response = await admin_client.patch(
        f"/api/v1/models/{model['id']}", json={"model_type": "embedding"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "MODEL_TYPE_IMMUTABLE"


async def test_fields_locked_for_referenced_embedding_model(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    model = await _create_model(admin_client, EMBEDDING_MODEL)
    await _add_kb_ref(session, model["id"])

    detail = (await admin_client.get(f"/api/v1/models/{model['id']}")).json()
    assert detail["fields_locked"] is True
    assert detail["ref_counts"] == {"app": 0, "kb": 1, "scheme": 0}

    locked_id = await admin_client.patch(
        f"/api/v1/models/{model['id']}", json={"litellm_model": "dashscope/text-embedding-v4"}
    )
    assert locked_id.status_code == 409
    assert locked_id.json()["code"] == "MODEL_FIELD_LOCKED"

    locked_dim = await admin_client.patch(
        f"/api/v1/models/{model['id']}", json={"embedding_dim": 2048}
    )
    assert locked_dim.status_code == 409
    assert locked_dim.json()["code"] == "MODEL_FIELD_LOCKED"

    renamed = await admin_client.patch(
        f"/api/v1/models/{model['id']}", json={"name": "改名后的向量模型"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "改名后的向量模型"


async def test_delete_blocked_by_refs_then_succeeds(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    model = await _create_model(admin_client, EMBEDDING_MODEL)
    resource_id = await _add_kb_ref(session, model["id"])

    blocked = await admin_client.delete(f"/api/v1/models/{model['id']}")
    assert blocked.status_code == 409
    body = blocked.json()
    assert body["code"] == "RESOURCE_IN_USE"
    refs = body["detail"]["refs"]
    assert refs and refs[0]["resource_id"] == resource_id
    assert refs[0]["name"] == "测试知识库"
    assert refs[0]["owner"]["username"]

    await session.execute(
        text("DELETE FROM resource_refs WHERE ref_resource_id = :id"), {"id": resource_id}
    )
    await session.commit()

    assert (await admin_client.delete(f"/api/v1/models/{model['id']}")).status_code == 204
    assert (await admin_client.get("/api/v1/models")).json()["total"] == 0


async def test_refs_endpoint_empty_and_filled(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    model = await _create_model(admin_client, EMBEDDING_MODEL)
    assert (await admin_client.get(f"/api/v1/models/{model['id']}/refs")).json() == []

    await _add_kb_ref(session, model["id"])
    refs = (await admin_client.get(f"/api/v1/models/{model['id']}/refs")).json()
    assert len(refs) == 1
    assert refs[0]["type"] == "kb"
    assert refs[0]["name"] == "测试知识库"


async def test_audit_actions_recorded(admin_client: AsyncClient, session: AsyncSession) -> None:
    model = await _create_model(admin_client)
    model_id = model["id"]

    await admin_client.patch(f"/api/v1/models/{model_id}", json={"name": "改名"})
    await admin_client.patch(f"/api/v1/models/{model_id}", json={"api_key": "sk-new-key-0001"})
    await admin_client.post(f"/api/v1/models/{model_id}:disable")
    await admin_client.post(f"/api/v1/models/{model_id}:enable")
    await admin_client.delete(f"/api/v1/models/{model_id}")

    rows = (await session.execute(text("SELECT action, detail FROM audit_logs ORDER BY id"))).all()
    actions = [row[0] for row in rows]
    for action in (
        "model.create",
        "model.update",
        "model.change_key",
        "model.disable",
        "model.enable",
        "model.delete",
    ):
        assert action in actions, actions
    # 更换密钥只记一次；不填密钥的编辑不产生 change_key。
    assert actions.count("model.change_key") == 1

    details = json.dumps([row[1] for row in rows], ensure_ascii=False)
    assert "sk-new-key-0001" not in details
    assert "api_key" not in details
