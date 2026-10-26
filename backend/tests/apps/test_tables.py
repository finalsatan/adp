"""应用与对话业务表：级联、唯一约束、循环外键与软删除（P4-02）。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.tables import App, AppUsageTarget, AppVersion
from app.chat.tables import AnswerFeedback, AnswerTrace, ChatAnswer, ChatTurn, Conversation
from app.core.schemas import Role
from app.resources.tables import Resource
from tests.factories import make_org, make_user


async def _make_app(session: AsyncSession, *, org, user, name: str = "测试应用") -> App:
    resource = Resource(type="app", name=name, org_id=org.id, owner_id=user.id, visibility="org")
    session.add(resource)
    await session.flush()
    app = App(id=resource.id, app_type="kb_qa", usage_scope="platform")
    session.add(app)
    await session.flush()
    return app


def _version(app_id: int, user_id: int, version_no: int = 1) -> AppVersion:
    return AppVersion(
        app_id=app_id,
        version_no=version_no,
        config={"schema_version": 1, "app_type": "kb_qa"},
        config_hash="hash-" + str(version_no),
        note="版本说明",
        published_by=user_id,
    )


async def _make_turn(
    session: AsyncSession, *, app: App, user_id: int, version_id: int | None = None
) -> tuple[Conversation, ChatTurn, ChatAnswer]:
    conversation = Conversation(app_id=app.id, user_id=user_id, title="新会话")
    session.add(conversation)
    await session.flush()
    turn = ChatTurn(conversation_id=conversation.id, seq=1, question="这是问题")
    session.add(turn)
    await session.flush()
    answer = ChatAnswer(
        turn_id=turn.id,
        seq=1,
        version_id=version_id,
        kind="generated",
        status="done",
        content="这是回答",
    )
    session.add(answer)
    await session.flush()
    return conversation, turn, answer


async def test_app_resource_cascade(session: AsyncSession) -> None:
    org = await make_org(session, "应用组织")
    user = await make_user(session, role=Role.builder, org=org)
    app = await _make_app(session, org=org, user=user)
    app_id = app.id

    # 删资源级联删 apps。
    resource = await session.get(Resource, app_id)
    assert resource is not None
    await session.delete(resource)
    await session.commit()
    assert await session.scalar(select(App.id).where(App.id == app_id)) is None


async def test_app_version_unique(session: AsyncSession) -> None:
    org = await make_org(session, "应用组织")
    user = await make_user(session, role=Role.builder, org=org)
    app = await _make_app(session, org=org, user=user)

    session.add(_version(app.id, user.id, 1))
    await session.commit()

    # 同一应用内版本号唯一。
    session.add(_version(app.id, user.id, 1))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_app_published_version_fk(session: AsyncSession) -> None:
    org = await make_org(session, "应用组织")
    user = await make_user(session, role=Role.builder, org=org)
    app = await _make_app(session, org=org, user=user)
    app_id = app.id

    version = _version(app_id, user.id, 1)
    session.add(version)
    await session.flush()
    app.published_version_id = version.id
    await session.commit()

    stored = await session.scalar(select(App.published_version_id).where(App.id == app_id))
    assert stored == version.id

    # 删版本后 published_version_id 置空（ON DELETE SET NULL）。
    await session.delete(version)
    await session.commit()
    assert await session.scalar(select(App.published_version_id).where(App.id == app_id)) is None


async def test_app_usage_target_unique(session: AsyncSession) -> None:
    org = await make_org(session, "应用组织")
    user = await make_user(session, role=Role.builder, org=org)
    app = await _make_app(session, org=org, user=user)

    session.add(AppUsageTarget(app_id=app.id, target_type="org", target_id=org.id))
    await session.commit()

    session.add(AppUsageTarget(app_id=app.id, target_type="org", target_id=org.id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_chat_answer_version_and_current_set_null(session: AsyncSession) -> None:
    org = await make_org(session, "应用组织")
    user = await make_user(session, role=Role.builder, org=org)
    app = await _make_app(session, org=org, user=user)
    version = _version(app.id, user.id, 1)
    session.add(version)
    await session.flush()

    _, turn, answer = await _make_turn(session, app=app, user_id=user.id, version_id=version.id)
    turn.current_answer_id = answer.id
    await session.commit()
    turn_id, answer_id = turn.id, answer.id

    # 删版本：回答记录的版本号置空。
    await session.delete(version)
    await session.commit()
    assert (
        await session.scalar(select(ChatAnswer.version_id).where(ChatAnswer.id == answer_id))
        is None
    )

    # 删回答：轮次的 current_answer_id 置空。
    answer = await session.get(ChatAnswer, answer_id)
    assert answer is not None
    await session.delete(answer)
    await session.commit()
    assert (
        await session.scalar(select(ChatTurn.current_answer_id).where(ChatTurn.id == turn_id))
        is None
    )


async def test_answer_trace_and_feedback_cascade(session: AsyncSession) -> None:
    org = await make_org(session, "应用组织")
    user = await make_user(session, role=Role.builder, org=org)
    app = await _make_app(session, org=org, user=user)
    _, _, answer = await _make_turn(session, app=app, user_id=user.id)
    answer_id = answer.id

    session.add(AnswerTrace(answer_id=answer_id, app_id=app.id, trace=[{"name": "retrieve"}]))
    session.add(AnswerFeedback(answer_id=answer_id, app_id=app.id, user_id=user.id, rating="up"))
    await session.commit()

    # 删回答级联删处理过程与反馈。
    answer = await session.get(ChatAnswer, answer_id)
    assert answer is not None
    await session.delete(answer)
    await session.commit()
    assert (
        await session.scalar(
            select(AnswerTrace.answer_id).where(AnswerTrace.answer_id == answer_id)
        )
        is None
    )
    assert (
        await session.scalar(select(AnswerFeedback.id).where(AnswerFeedback.answer_id == answer_id))
        is None
    )


async def test_answer_feedback_unique(session: AsyncSession) -> None:
    org = await make_org(session, "应用组织")
    user = await make_user(session, role=Role.builder, org=org)
    app = await _make_app(session, org=org, user=user)
    _, _, answer = await _make_turn(session, app=app, user_id=user.id)

    session.add(AnswerFeedback(answer_id=answer.id, app_id=app.id, user_id=user.id, rating="up"))
    await session.commit()

    # 同一用户对同一回答只能有一行。
    session.add(AnswerFeedback(answer_id=answer.id, app_id=app.id, user_id=user.id, rating="down"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_conversation_soft_delete(session: AsyncSession) -> None:
    org = await make_org(session, "应用组织")
    user = await make_user(session, role=Role.builder, org=org)
    app = await _make_app(session, org=org, user=user)
    conversation = Conversation(app_id=app.id, user_id=user.id, title="新会话")
    session.add(conversation)
    await session.commit()
    conversation_id = conversation.id

    conversation.deleted_at = datetime.now(UTC)
    await session.commit()

    # 软删除后行仍在，只是 deleted_at 有值。
    stored = await session.scalar(
        select(Conversation.deleted_at).where(Conversation.id == conversation_id)
    )
    assert stored is not None
