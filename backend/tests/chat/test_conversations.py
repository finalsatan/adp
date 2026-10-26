"""会话与轮次读写测试（P4-09）。

连真实 PostgreSQL；会话列表 / 新建 / 重命名 / 软删除、`GET turns`、重新生成落库与
`generating` 超时按“已中断”显示。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import config_hash, default_config
from app.apps.tables import App, AppUsageTarget, AppVersion
from app.chat import conversations
from app.chat.schemas import AnswerStatus
from app.chat.tables import AnswerFeedback, ChatAnswer, Conversation
from app.core.schemas import Role
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_app(session: AsyncSession, org, owner, *, name: str | None = None) -> App:
    resource = Resource(
        type="app",
        name=name or f"应用-{uuid.uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility="org",
    )
    session.add(resource)
    await session.flush()
    app = App(
        id=resource.id,
        app_type="kb_qa",
        draft_config=default_config().model_dump(mode="json"),
        draft_revision=1,
        usage_scope="platform",
    )
    session.add(app)
    config = default_config().model_dump(mode="json")
    version = AppVersion(
        app_id=resource.id,
        version_no=1,
        config=config,
        config_hash=config_hash(config),
        note="初版",
        published_by=owner.id,
    )
    session.add(version)
    await session.flush()
    app.published_version_id = version.id
    await session.commit()
    await session.refresh(app)
    return app


async def _conversation(
    session: AsyncSession, app: App, user_id: int, title: str = "新会话"
) -> Conversation:
    conversation = Conversation(app_id=app.id, user_id=user_id, title=title)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


# ---- 会话列表 / 新建 / 重命名 / 软删除 ----


async def test_conversation_crud_and_soft_delete(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "会话组织")
    owner = await make_user(session, Role.builder, org)
    app = await make_app(session, org, owner)
    user = await make_user(session, Role.end_user, org)
    login_as(client, user)

    created = await client.post(f"/api/v1/chat/apps/{app.id}/conversations", json={})
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    assert created.json()["title"] == "新会话"

    listed = await client.get(f"/api/v1/chat/apps/{app.id}/conversations")
    assert [item["id"] for item in listed.json()["items"]] == [conversation_id]

    renamed = await client.patch(
        f"/api/v1/conversations/{conversation_id}", json={"title": "我的会话"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "我的会话"

    deleted = await client.delete(f"/api/v1/conversations/{conversation_id}")
    assert deleted.status_code == 204

    listed = await client.get(f"/api/v1/chat/apps/{app.id}/conversations")
    assert listed.json()["items"] == []
    # 软删除：行还在，deleted_at 有值。
    stored = await session.scalar(
        sa.select(Conversation.deleted_at).where(Conversation.id == conversation_id)
    )
    assert stored is not None


async def test_conversation_of_other_user_not_found(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "会话组织")
    owner = await make_user(session, Role.builder, org)
    app = await make_app(session, org, owner)
    owner_user = await make_user(session, Role.end_user, org)
    other = await make_user(session, Role.end_user, org)
    conversation = await _conversation(session, app, owner_user.id)

    login_as(client, other)
    response = await client.patch(
        f"/api/v1/conversations/{conversation.id}", json={"title": "改别人的"}
    )
    assert response.status_code == 404


async def test_removed_from_scope_returns_403(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "会话组织")
    owner = await make_user(session, Role.builder, org)
    app = await make_app(session, org, owner)
    user = await make_user(session, Role.end_user, org)
    conversation = await _conversation(session, app, user.id)

    # 使用范围改为只允许别人。
    other = await make_user(session, Role.end_user, org)
    app.usage_scope = "users"
    session.add(AppUsageTarget(app_id=app.id, target_type="user", target_id=other.id))
    await session.commit()

    login_as(client, user)
    turns = await client.get(f"/api/v1/conversations/{conversation.id}/turns")
    assert turns.status_code == 403
    assert turns.json()["code"] == "APP_NOT_IN_SCOPE"


# ---- 第一问设置标题、GET turns ----


async def test_first_question_sets_title(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "会话组织")
    owner = await make_user(session, Role.builder, org)
    app = await make_app(session, org, owner)
    user = await make_user(session, Role.end_user, org)
    conversation = await _conversation(session, app, user.id)
    question = "考勤制度是怎么规定的？" * 5

    await conversations.create_turn(
        session, conversation, question=question, version_id=app.published_version_id
    )
    await session.refresh(conversation)
    assert conversation.title == question[:20]
    assert len(conversation.title) == 20


async def test_list_turns_returns_question_and_current_answer(
    client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, "会话组织")
    owner = await make_user(session, Role.builder, org)
    app = await make_app(session, org, owner)
    user = await make_user(session, Role.end_user, org)
    conversation = await _conversation(session, app, user.id)
    turn, answer = await conversations.create_turn(
        session, conversation, question="什么是考勤？", version_id=app.published_version_id
    )
    answer.content = "考勤是……"
    answer.status = AnswerStatus.done.value
    await session.commit()

    login_as(client, user)
    response = await client.get(f"/api/v1/conversations/{conversation.id}/turns")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == turn.id
    assert data[0]["question"] == "什么是考勤？"
    assert data[0]["current_answer_id"] == answer.id
    assert data[0]["answers"][0]["content"] == "考勤是……"
    assert data[0]["answers"][0]["status"] == "done"
    assert data[0]["answers"][0]["version_no"] == 1


async def test_turns_include_own_feedback(client: AsyncClient, session: AsyncSession) -> None:
    """刷新后能看到自己的点赞 / 点踩（带原因与纠错），取消后为 null（P4-13 缺陷修复）。"""
    org = await make_org(session, "会话组织")
    owner = await make_user(session, Role.builder, org)
    app = await make_app(session, org, owner)
    user = await make_user(session, Role.end_user, org)
    conversation = await _conversation(session, app, user.id)
    _, answer = await conversations.create_turn(
        session, conversation, question="什么是考勤？", version_id=app.published_version_id
    )
    answer.content = "考勤是……"
    answer.status = AnswerStatus.done.value
    await session.commit()

    login_as(client, user)
    turns_url = f"/api/v1/conversations/{conversation.id}/turns"

    liked = await client.put(f"/api/v1/answers/{answer.id}/feedback", json={"rating": "up"})
    assert liked.status_code == 200, liked.text
    data = (await client.get(turns_url)).json()
    feedback = data[0]["answers"][0]["feedback"]
    assert feedback is not None
    assert feedback["rating"] == "up"
    assert feedback["reasons"] == []
    assert feedback["correction"] is None

    disliked = await client.put(
        f"/api/v1/answers/{answer.id}/feedback",
        json={"rating": "down", "reasons": ["wrong_answer"], "correction": "应为打卡……"},
    )
    assert disliked.status_code == 200, disliked.text
    data = (await client.get(turns_url)).json()
    feedback = data[0]["answers"][0]["feedback"]
    assert feedback is not None
    assert feedback["rating"] == "down"
    assert feedback["reasons"] == ["wrong_answer"]
    assert feedback["correction"] == "应为打卡……"

    removed = await client.delete(f"/api/v1/answers/{answer.id}/feedback")
    assert removed.status_code == 204
    data = (await client.get(turns_url)).json()
    assert data[0]["answers"][0]["feedback"] is None


async def test_turns_feedback_excludes_others(client: AsyncClient, session: AsyncSession) -> None:
    """别人的反馈不出现在自己的 turns 里（只返回本人的）。"""
    org = await make_org(session, "会话组织")
    owner = await make_user(session, Role.builder, org)
    app = await make_app(session, org, owner)
    user = await make_user(session, Role.end_user, org)
    other = await make_user(session, Role.end_user, org)
    conversation = await _conversation(session, app, user.id)
    _, answer = await conversations.create_turn(
        session, conversation, question="什么是考勤？", version_id=app.published_version_id
    )
    answer.content = "考勤是……"
    answer.status = AnswerStatus.done.value
    session.add(
        AnswerFeedback(
            answer_id=answer.id, app_id=app.id, user_id=other.id, rating="up", reasons=[]
        )
    )
    await session.commit()

    login_as(client, user)
    response = await client.get(f"/api/v1/conversations/{conversation.id}/turns")
    assert response.status_code == 200, response.text
    assert response.json()[0]["answers"][0]["feedback"] is None


async def test_regeneration_creates_new_answer(client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, "会话组织")
    owner = await make_user(session, Role.builder, org)
    app = await make_app(session, org, owner)
    user = await make_user(session, Role.end_user, org)
    conversation = await _conversation(session, app, user.id)
    turn, first = await conversations.create_turn(
        session, conversation, question="什么是考勤？", version_id=app.published_version_id
    )

    second = await conversations.create_regeneration(
        session, turn, version_id=app.published_version_id
    )
    assert second.seq == 2
    await session.refresh(turn)
    assert turn.current_answer_id == second.id

    turns = await conversations.list_turns(session, conversation.id)
    assert [answer.id for answer in turns[0].answers] == [first.id, second.id]


async def test_stale_generating_shown_as_stopped(session: AsyncSession) -> None:
    org = await make_org(session, "会话组织")
    owner = await make_user(session, Role.builder, org)
    app = await make_app(session, org, owner)
    user = await make_user(session, Role.end_user, org)
    conversation = await _conversation(session, app, user.id)
    turn, answer = await conversations.create_turn(
        session, conversation, question="什么是考勤？", version_id=app.published_version_id
    )
    # 11 分钟没有更新：读取时按“已中断”显示（9.2）。
    await session.execute(
        sa.update(ChatAnswer)
        .where(ChatAnswer.id == answer.id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=11))
    )
    await session.commit()

    turns = await conversations.list_turns(session, conversation.id)
    assert turns[0].answers[0].status == AnswerStatus.stopped
    # 数据库里的状态仍是 generating，不做定时任务修正。
    stored = await session.get(ChatAnswer, answer.id)
    assert stored is not None and stored.status == AnswerStatus.generating.value

    # 10 分钟以内仍按 generating 显示。
    await session.execute(
        sa.update(ChatAnswer)
        .where(ChatAnswer.id == answer.id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await session.commit()
    turns = await conversations.list_turns(session, conversation.id)
    assert turns[0].answers[0].status == AnswerStatus.generating
    assert turn.id is not None


def test_extract_cited() -> None:
    assert conversations.extract_cited("用了[1]和【3】，还有[1]") == [1, 3]
    assert conversations.extract_cited("没有角标") == []
    assert conversations.extract_cited("") == []
