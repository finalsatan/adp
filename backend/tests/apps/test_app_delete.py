"""应用删除：资源软删除、业务行保留、引用登记清理与重建（P4-03）。"""

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.tables import App, AppUsageTarget
from app.chat.tables import AnswerFeedback, AnswerTrace, ChatAnswer, ChatTurn, Conversation
from app.core.schemas import Role
from app.resources.tables import Resource, ResourceRef
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def create_app(client: AsyncClient, *, name: str):
    return await client.post(
        "/api/v1/apps",
        json={"app_type": "kb_qa", "name": name, "visibility": "org"},
    )


async def _count(session: AsyncSession, model, **filters) -> int:
    stmt = sa.select(sa.func.count()).select_from(model)
    for column, value in filters.items():
        stmt = stmt.where(getattr(model, column) == value)
    return int(await session.scalar(stmt) or 0)


async def test_delete_app_cleans_refs_keeps_business_rows(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "删除应用组织")
    owner = await make_user(session, Role.builder, org, username="delete_app_owner")
    login_as(client, owner)

    created = await create_app(client, name="待删除应用")
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]

    # 应用引用了某个模型 / 知识库（引用登记），删除应用要清掉这些登记。
    session.add(ResourceRef(target_type="resource", target_id=999, ref_resource_id=app_id))
    # 会话、轮次、回答、处理过程、反馈：删除应用后都要保留。
    conversation = Conversation(app_id=app_id, user_id=owner.id, title="新会话")
    session.add(conversation)
    await session.flush()
    turn = ChatTurn(conversation_id=conversation.id, seq=1, question="问题")
    session.add(turn)
    await session.flush()
    answer = ChatAnswer(turn_id=turn.id, seq=1, kind="generated", status="done", content="回答")
    session.add(answer)
    await session.flush()
    session.add(AnswerTrace(answer_id=answer.id, app_id=app_id, trace=[]))
    session.add(AnswerFeedback(answer_id=answer.id, app_id=app_id, user_id=owner.id, rating="up"))
    await session.commit()
    conversation_id, answer_id = conversation.id, answer.id

    deleted = await client.delete(f"/api/v1/apps/{app_id}")
    assert deleted.status_code == 204, deleted.text

    session.expire_all()
    resource = await session.get(Resource, app_id)
    assert resource is not None and resource.deleted_at is not None

    # apps、使用范围名单与会话 / 日志 / 反馈行都在（软删除，不硬删业务数据）。
    assert await session.get(App, app_id) is not None
    assert await _count(session, AppUsageTarget, app_id=app_id) == 1
    assert await _count(session, Conversation, id=conversation_id) == 1
    assert await _count(session, ChatAnswer, id=answer_id) == 1
    assert await _count(session, AnswerTrace, answer_id=answer_id) == 1
    assert await _count(session, AnswerFeedback, answer_id=answer_id) == 1

    # 该应用登记的 resource_refs 被清掉。
    assert await _count(session, ResourceRef, ref_resource_id=app_id) == 0

    # 同名可以再建（软删除的资源不占名字）。
    again = await create_app(client, name="待删除应用")
    assert again.status_code == 201, again.text
    assert again.json()["id"] != app_id
