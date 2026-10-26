"""发布、发布历史与恢复测试（P4-04）：校验、审计、引用登记并集。"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.tables import App, AppVersion
from app.audit.tables import AuditLog
from app.core.schemas import Role
from app.kb.tables import KnowledgeBase
from app.models.tables import Model
from app.resources.tables import Resource, ResourceRef
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_chat_model(session: AsyncSession, owner, *, status: str = "enabled") -> Model:
    model = Model(
        name=f"对话模型-{uuid.uuid4().hex[:8]}",
        model_type="chat",
        litellm_model="openai/gpt-4o-mini",
        status=status,
        created_by=owner.id,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def make_embedding_model(
    session: AsyncSession, owner, *, dim: int = 1024, status: str = "enabled"
) -> Model:
    model = Model(
        name=f"向量模型-{uuid.uuid4().hex[:8]}",
        model_type="embedding",
        litellm_model="openai/text-embedding-3-small",
        embedding_dim=dim,
        embedding_max_input=8192,
        status=status,
        created_by=owner.id,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model


async def make_kb(
    session: AsyncSession,
    org,
    owner,
    embedding_model: Model,
    *,
    kind: str = "doc",
    visibility: str = "org",
    name: str | None = None,
) -> KnowledgeBase:
    resource = Resource(
        type="kb",
        name=name or f"知识库-{uuid.uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility=visibility,
    )
    session.add(resource)
    await session.flush()
    kb = KnowledgeBase(
        id=resource.id,
        kind=kind,
        embedding_model_id=embedding_model.id,
        embedding_dim=embedding_model.embedding_dim or 1024,
        index_table=f"kb_index.kb_{resource.id}",
    )
    session.add(kb)
    await session.commit()
    await session.refresh(kb)
    return kb


async def create_app(client: AsyncClient, *, name: str | None = None, visibility: str = "org"):
    return await client.post(
        "/api/v1/apps",
        json={
            "app_type": "kb_qa",
            "name": name or f"应用-{uuid.uuid4().hex[:8]}",
            "visibility": visibility,
        },
    )


def draft_config(
    *,
    chat_model_id: int | None = None,
    kb_ids: tuple[int, ...] = (),
    doc_rerank: bool = False,
    extra_kbs: list[dict] | None = None,
    intent_model_id: int | None = None,
) -> dict:
    kbs = [{"kb_id": kb_id} for kb_id in kb_ids]
    if extra_kbs:
        kbs.extend(extra_kbs)
    return {
        "app_type": "kb_qa",
        "model": {"chat_model_id": chat_model_id} if chat_model_id is not None else None,
        "intent": {"model_id": intent_model_id},
        "knowledge": {
            "kbs": kbs,
            "doc": {"rerank": {"enabled": doc_rerank}},
        },
    }


async def set_draft(client: AsyncClient, app_id: int, revision: int, config: dict):
    response = await client.put(
        f"/api/v1/apps/{app_id}/draft",
        json={"draft_revision": revision, "config": config},
    )
    assert response.status_code == 200, response.text
    return response.json()["draft_revision"]


async def publish(client: AsyncClient, app_id: int, note: str = "初版"):
    return await client.post(f"/api/v1/apps/{app_id}:publish", json={"note": note})


async def refs_of(session: AsyncSession, app_id: int) -> set[tuple[str, int]]:
    rows = (
        (await session.execute(sa.select(ResourceRef).where(ResourceRef.ref_resource_id == app_id)))
        .scalars()
        .all()
    )
    return {(row.target_type, row.target_id) for row in rows}


async def test_publish_and_history(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "发布组织")
    builder = await make_user(session, Role.builder, org, username="publish_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()
    chat_model = await make_chat_model(session, builder)
    embedding = await make_embedding_model(session, builder)
    kb = await make_kb(session, org, builder, embedding)
    config = draft_config(chat_model_id=chat_model.id, kb_ids=(kb.id,))
    revision = await set_draft(client, app["id"], 1, config)

    first = await publish(client, app["id"], "初版")
    assert first.status_code == 200, first.text
    assert first.json()["version_no"] == 1
    assert first.json()["is_current"] is True
    assert first.json()["note"] == "初版"
    assert first.json()["published_by"]["id"] == builder.id

    app_row = await session.get(App, app["id"])
    assert app_row is not None and app_row.published_version_id == first.json()["id"]
    version_row = await session.get(AppVersion, first.json()["id"])
    assert version_row is not None
    assert version_row.config_hash == first.json()["config_hash"]
    assert version_row.version_no == 1

    # 改草稿后再发布，版本号递增。
    revision = await set_draft(client, app["id"], revision, {**config, "system_prompt": "第二版"})
    second = await publish(client, app["id"], "第二版")
    assert second.status_code == 200, second.text
    assert second.json()["version_no"] == 2
    assert second.json()["is_current"] is True

    history = await client.get(f"/api/v1/apps/{app['id']}/versions")
    assert history.status_code == 200
    assert history.json()["total"] == 2
    assert [item["version_no"] for item in history.json()["items"]] == [2, 1]
    assert [item["is_current"] for item in history.json()["items"]] == [True, False]

    detail = await client.get(f"/api/v1/apps/{app['id']}/versions/1")
    assert detail.status_code == 200
    assert detail.json()["version_no"] == 1
    assert detail.json()["is_current"] is False

    missing = await client.get(f"/api/v1/apps/{app['id']}/versions/99")
    assert missing.status_code == 404


async def test_publish_draft_restore_and_audit(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "恢复组织")
    builder = await make_user(session, Role.builder, org, username="restore_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()
    chat_model = await make_chat_model(session, builder)
    embedding = await make_embedding_model(session, builder)
    kb = await make_kb(session, org, builder, embedding)
    config = draft_config(chat_model_id=chat_model.id, kb_ids=(kb.id,))
    revision = await set_draft(client, app["id"], 1, config)
    first = await publish(client, app["id"])
    assert first.status_code == 200
    version_one_hash = first.json()["config_hash"]

    # 发布后改草稿不影响已发布版本。
    revision = await set_draft(
        client, app["id"], revision, {**config, "system_prompt": "改过的提示词"}
    )
    detail = await client.get(f"/api/v1/apps/{app['id']}/versions/1")
    assert detail.json()["config_hash"] == version_one_hash
    assert detail.json()["config"]["system_prompt"] == ""

    restored = await client.post(f"/api/v1/apps/{app['id']}/versions/1:restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["draft_revision"] == revision + 1
    assert restored.json()["config"]["system_prompt"] == ""
    assert restored.json()["has_changes"] is False

    logs = (
        (
            await session.execute(
                sa.select(AuditLog).where(
                    AuditLog.target_type == "app", AuditLog.target_id == app["id"]
                )
            )
        )
        .scalars()
        .all()
    )
    actions = {log.action for log in logs}
    assert {"app.publish", "app.restore"} <= actions
    publish_log = next(log for log in logs if log.action == "app.publish")
    assert publish_log.detail == {"version_no": 1}


async def test_publish_validation_models(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "校验模型组织")
    builder = await make_user(session, Role.builder, org, username="validate_model_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()

    no_model = await publish(client, app["id"])
    assert no_model.status_code == 422
    assert no_model.json()["code"] == "PUBLISH_CHECK_FAILED"
    problems = no_model.json()["detail"]["problems"]
    assert {"code", "message", "field"} <= set(problems[0])
    assert any(p["code"] == "MODEL_REQUIRED" for p in problems)

    disabled = await make_chat_model(session, builder, status="disabled")
    revision = await set_draft(client, app["id"], 1, draft_config(chat_model_id=disabled.id))
    unavailable = await publish(client, app["id"])
    assert unavailable.status_code == 422
    assert any(
        p["code"] == "MODEL_UNAVAILABLE" and p["field"] == "model.chat_model_id"
        for p in unavailable.json()["detail"]["problems"]
    )

    enabled = await make_chat_model(session, builder)
    await set_draft(
        client,
        app["id"],
        revision,
        draft_config(chat_model_id=enabled.id, intent_model_id=disabled.id),
    )
    intent_problem = await publish(client, app["id"])
    assert intent_problem.status_code == 422
    assert any(
        p["code"] == "MODEL_UNAVAILABLE" and p["field"] == "intent.model_id"
        for p in intent_problem.json()["detail"]["problems"]
    )


async def test_publish_validation_kb(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "校验知识库组织")
    builder = await make_user(session, Role.builder, org, username="validate_kb_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()
    chat_model = await make_chat_model(session, builder)
    embedding = await make_embedding_model(session, builder)

    no_kb = await publish(client, app["id"])
    assert no_kb.status_code == 422
    assert any(p["code"] == "KB_REQUIRED" for p in no_kb.json()["detail"]["problems"])

    revision = await set_draft(
        client,
        app["id"],
        1,
        draft_config(chat_model_id=chat_model.id, extra_kbs=[{"kb_id": 999999}]),
    )
    not_found = await publish(client, app["id"])
    assert not_found.status_code == 422
    assert any(p["code"] == "KB_NOT_FOUND" for p in not_found.json()["detail"]["problems"])

    # 引用的知识库被删除后发布被拦下。
    kb = await make_kb(session, org, builder, embedding)
    revision = await set_draft(
        client, app["id"], revision, draft_config(chat_model_id=chat_model.id, kb_ids=(kb.id,))
    )
    await session.execute(sa.delete(KnowledgeBase).where(KnowledgeBase.id == kb.id))
    await session.execute(
        sa.update(Resource)
        .where(Resource.id == kb.id)
        .values(deleted_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    )
    await session.commit()
    deleted = await publish(client, app["id"])
    assert deleted.status_code == 422
    assert any(p["code"] == "KB_NOT_FOUND" for p in deleted.json()["detail"]["problems"])


async def test_publish_validation_rerank(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "校验重排组织")
    builder = await make_user(session, Role.builder, org, username="validate_rerank_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()
    chat_model = await make_chat_model(session, builder)
    embedding_a = await make_embedding_model(session, builder, dim=1024)
    embedding_b = await make_embedding_model(session, builder, dim=768)
    kb_a = await make_kb(session, org, builder, embedding_a, name="校验重排库A")
    kb_b = await make_kb(session, org, builder, embedding_b, name="校验重排库B")

    await set_draft(
        client,
        app["id"],
        1,
        draft_config(chat_model_id=chat_model.id, kb_ids=(kb_a.id, kb_b.id), doc_rerank=True),
    )
    # 绕过保存校验，直接把草稿改成未开启重排（模拟历史草稿）。
    app_row = await session.get(App, app["id"])
    assert app_row is not None
    app_row.draft_config = draft_config(
        chat_model_id=chat_model.id, kb_ids=(kb_a.id, kb_b.id), doc_rerank=False
    )
    await session.commit()

    blocked = await publish(client, app["id"])
    assert blocked.status_code == 422
    assert any(p["code"] == "DOC_RERANK_REQUIRED" for p in blocked.json()["detail"]["problems"])


async def test_publish_validation_filters(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "校验过滤组织")
    builder = await make_user(session, Role.builder, org, username="validate_filter_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()
    chat_model = await make_chat_model(session, builder)
    embedding = await make_embedding_model(session, builder)
    kb = await make_kb(session, org, builder, embedding)

    await set_draft(
        client,
        app["id"],
        1,
        draft_config(
            chat_model_id=chat_model.id,
            extra_kbs=[{"kb_id": kb.id, "filter": {"folder_ids": [999999]}}],
        ),
    )
    folder_problem = await publish(client, app["id"])
    assert folder_problem.status_code == 422
    assert any(p["code"] == "FOLDER_NOT_FOUND" for p in folder_problem.json()["detail"]["problems"])

    await set_draft(
        client,
        app["id"],
        2,
        draft_config(
            chat_model_id=chat_model.id,
            extra_kbs=[
                {
                    "kb_id": kb.id,
                    "filter": {
                        "metadata": {
                            "op": "and",
                            "conditions": [{"field_id": 999999, "operator": "eq", "value": "x"}],
                        }
                    },
                }
            ],
        ),
    )
    metadata_problem = await publish(client, app["id"])
    assert metadata_problem.status_code == 422
    assert any(
        p["code"] == "METADATA_FIELD_NOT_FOUND"
        for p in metadata_problem.json()["detail"]["problems"]
    )


async def test_publish_resource_refs_union(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "引用并集组织")
    builder = await make_user(session, Role.builder, org, username="refs_union_builder")
    login_as(client, builder)
    app = (await create_app(client)).json()
    chat_model = await make_chat_model(session, builder)
    embedding = await make_embedding_model(session, builder)
    kb_a = await make_kb(session, org, builder, embedding, name="并集库A")
    kb_b = await make_kb(session, org, builder, embedding, name="并集库B")

    revision = await set_draft(
        client, app["id"], 1, draft_config(chat_model_id=chat_model.id, kb_ids=(kb_a.id,))
    )
    assert await refs_of(session, app["id"]) == {("model", chat_model.id), ("resource", kb_a.id)}

    first = await publish(client, app["id"])
    assert first.status_code == 200
    assert await refs_of(session, app["id"]) == {("model", chat_model.id), ("resource", kb_a.id)}

    revision = await set_draft(
        client, app["id"], revision, draft_config(chat_model_id=chat_model.id, kb_ids=(kb_b.id,))
    )
    # 草稿 ∪ 当前发布版本（v1 引用 A，草稿引用 B）。
    assert await refs_of(session, app["id"]) == {
        ("model", chat_model.id),
        ("resource", kb_a.id),
        ("resource", kb_b.id),
    }

    second = await publish(client, app["id"])
    assert second.status_code == 200
    # 当前发布版本换成 v2 后，历史版本 v1 的引用不再登记。
    assert await refs_of(session, app["id"]) == {("model", chat_model.id), ("resource", kb_b.id)}
