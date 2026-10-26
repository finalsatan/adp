"""用户反馈测试（P4-13）。

连真实 PostgreSQL、Redis。覆盖终端用户点赞 / 点踩 / 纠错 / 取消（同一用户再次点踩为
更新）、归属与使用范围判断，以及搭建者反馈列表的筛选、排序、分页、统计与“编辑”权限。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.config import config_hash, default_config
from app.apps.tables import App, AppUsageTarget, AppVersion
from app.chat.tables import AnswerFeedback, ChatAnswer, ChatTurn, Conversation
from app.core.schemas import Role
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user

FEEDBACK_URL = "/api/v1/answers/{answer_id}/feedback"
LIST_URL = "/api/v1/apps/{app_id}/feedback"


def login_as(client: AsyncClient, user) -> None:
    client.cookies.clear()
    client.cookies.update(auth_cookies(user))


async def make_app(
    session: AsyncSession,
    org,
    owner,
    *,
    usage_scope: str = "platform",
    visibility: str = "org",
) -> App:
    resource = Resource(
        type="app",
        name=f"应用-{uuid.uuid4().hex[:8]}",
        org_id=org.id,
        owner_id=owner.id,
        visibility=visibility,
    )
    session.add(resource)
    await session.flush()
    config = default_config().model_dump(mode="json")
    app = App(
        id=resource.id,
        app_type="kb_qa",
        draft_config=config,
        draft_revision=1,
        usage_scope=usage_scope,
    )
    session.add(app)
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


async def make_conversation(
    session: AsyncSession, app: App, user, *, title: str = "会话"
) -> Conversation:
    conversation = Conversation(app_id=app.id, user_id=user.id, title=title)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def add_turn(
    session: AsyncSession,
    conversation: Conversation,
    app: App,
    *,
    seq: int = 1,
    question: str = "问题",
    content: str = "回答内容",
) -> tuple[ChatTurn, ChatAnswer]:
    turn = ChatTurn(conversation_id=conversation.id, seq=seq, question=question)
    session.add(turn)
    await session.flush()
    answer = ChatAnswer(
        turn_id=turn.id,
        seq=1,
        version_id=app.published_version_id,
        kind="generated",
        status="done",
        content=content,
    )
    session.add(answer)
    await session.flush()
    turn.current_answer_id = answer.id
    await session.commit()
    await session.refresh(turn)
    await session.refresh(answer)
    return turn, answer


async def add_feedback(
    session: AsyncSession,
    app: App,
    answer: ChatAnswer,
    user,
    rating: str,
    *,
    reasons: list[str] | None = None,
    correction: str | None = None,
    created_at: datetime | None = None,
) -> AnswerFeedback:
    feedback = AnswerFeedback(
        answer_id=answer.id,
        app_id=app.id,
        user_id=user.id,
        rating=rating,
        reasons=reasons or [],
        correction=correction,
    )
    if created_at is not None:
        feedback.created_at = created_at
    session.add(feedback)
    await session.commit()
    await session.refresh(feedback)
    return feedback


def _feedback_count(answer_id: int, user_id: int):
    return (
        sa.select(sa.func.count())
        .select_from(AnswerFeedback)
        .where(AnswerFeedback.answer_id == answer_id, AnswerFeedback.user_id == user_id)
    )


# ---- 终端用户写入 / 取消 ----


async def test_put_feedback_insert_and_update(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"反馈组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner)
    conversation = await make_conversation(session, app_row, user)
    _, answer = await add_turn(session, conversation, app_row, question="病假要什么材料？")

    login_as(client, user)
    liked = await client.put(FEEDBACK_URL.format(answer_id=answer.id), json={"rating": "up"})
    assert liked.status_code == 200, liked.text
    body = liked.json()
    assert body["answer_id"] == answer.id
    assert body["rating"] == "up"
    assert body["reasons"] == []
    assert body["correction"] is None

    # 再次点踩（带原因与纠错）为更新，不新增行。
    down = await client.put(
        FEEDBACK_URL.format(answer_id=answer.id),
        json={
            "rating": "down",
            "reasons": ["wrong_answer", "other"],
            "correction": "需要二级以上医院的诊断证明和病历。",
        },
    )
    assert down.status_code == 200, down.text
    updated = down.json()
    assert updated["id"] == body["id"]
    assert updated["rating"] == "down"
    assert updated["reasons"] == ["wrong_answer", "other"]
    assert updated["correction"] == "需要二级以上医院的诊断证明和病历。"
    assert updated["created_at"] == body["created_at"]
    assert await session.scalar(_feedback_count(answer.id, user.id)) == 1

    # 改回点赞：原因与纠错清空。
    again = await client.put(FEEDBACK_URL.format(answer_id=answer.id), json={"rating": "up"})
    assert again.status_code == 200
    assert again.json()["reasons"] == []
    assert again.json()["correction"] is None


async def test_delete_feedback_removes_row(app, client: AsyncClient, session: AsyncSession) -> None:
    org = await make_org(session, f"取消组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner)
    conversation = await make_conversation(session, app_row, user)
    _, answer = await add_turn(session, conversation, app_row)

    login_as(client, user)
    put = await client.put(
        FEEDBACK_URL.format(answer_id=answer.id),
        json={"rating": "down", "reasons": ["not_answered"]},
    )
    assert put.status_code == 200
    assert await session.scalar(_feedback_count(answer.id, user.id)) == 1

    deleted = await client.delete(FEEDBACK_URL.format(answer_id=answer.id))
    assert deleted.status_code == 204
    assert await session.scalar(_feedback_count(answer.id, user.id)) == 0
    # 再删一次仍成功（幂等）。
    assert (await client.delete(FEEDBACK_URL.format(answer_id=answer.id))).status_code == 204


async def test_feedback_ownership_and_scope(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"范围组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    other = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner)
    conversation = await make_conversation(session, app_row, user)
    _, answer = await add_turn(session, conversation, app_row)

    # 他人的回答返回 403。
    login_as(client, other)
    forbidden = await client.put(FEEDBACK_URL.format(answer_id=answer.id), json={"rating": "up"})
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "PERMISSION_DENIED"

    # 回答不存在返回 404。
    login_as(client, user)
    missing = await client.put(FEEDBACK_URL.format(answer_id=999999), json={"rating": "up"})
    assert missing.status_code == 404

    # 被移出使用范围后返回 403。
    app_row.usage_scope = "users"
    session.add(AppUsageTarget(app_id=app_row.id, target_type="user", target_id=other.id))
    await session.commit()
    out_of_scope = await client.put(FEEDBACK_URL.format(answer_id=answer.id), json={"rating": "up"})
    assert out_of_scope.status_code == 403
    assert out_of_scope.json()["code"] == "APP_NOT_IN_SCOPE"


# ---- 搭建者反馈列表 ----


async def test_feedback_list_filters_order_and_pagination(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"列表组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner)
    now = datetime.now(UTC)

    first_conv = await make_conversation(session, app_row, user, title="病假")
    _, first_answer = await add_turn(
        session, first_conv, app_row, question="病假超过 3 天要什么材料？", content="需要诊断证明。"
    )
    await add_feedback(
        session,
        app_row,
        first_answer,
        user,
        "down",
        reasons=["wrong_answer"],
        correction="还需要病历。",
        created_at=now,
    )

    second_conv = await make_conversation(session, app_row, user, title="公积金")
    _, second_answer = await add_turn(session, second_conv, app_row, question="公积金能付房租吗？")
    await add_feedback(
        session, app_row, second_answer, user, "up", created_at=now - timedelta(minutes=1)
    )

    third_conv = await make_conversation(session, app_row, user, title="离职证明")
    _, third_answer = await add_turn(session, third_conv, app_row, question="离职证明多久能开？")
    await add_feedback(
        session,
        app_row,
        third_answer,
        user,
        "down",
        reasons=["not_answered"],
        created_at=now - timedelta(minutes=2),
    )

    old_conv = await make_conversation(session, app_row, user, title="旧反馈")
    _, old_answer = await add_turn(session, old_conv, app_row, question="很久以前的问题")
    await add_feedback(
        session,
        app_row,
        old_answer,
        user,
        "down",
        correction="",
        created_at=now - timedelta(days=10),
    )

    login_as(client, owner)
    all_time = await client.get(LIST_URL.format(app_id=app_row.id), params={"range": "all"})
    assert all_time.status_code == 200, all_time.text
    body = all_time.json()
    assert body["total"] == 4
    assert [item["conversation_id"] for item in body["items"]] == [
        first_conv.id,
        second_conv.id,
        third_conv.id,
        old_conv.id,
    ]
    item = body["items"][0]
    assert item["answer_id"] == first_answer.id
    assert item["user"] == {"id": user.id, "username": user.username, "name": user.name}
    assert item["org"] == {"id": org.id, "name": org.name}
    assert item["question"] == "病假超过 3 天要什么材料？"
    assert item["answer_excerpt"] == "需要诊断证明。"
    assert item["rating"] == "down"
    assert item["reasons"] == ["wrong_answer"]
    assert item["correction"] == "还需要病历。"
    assert item["handled"] is False

    # 按点赞 / 点踩筛选。
    up = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "rating": "up"}
    )
    assert [item["conversation_id"] for item in up.json()["items"]] == [second_conv.id]
    down = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "rating": "down"}
    )
    assert down.json()["total"] == 3

    # 有无纠错：空串算没有纠错。
    with_correction = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "has_correction": "true"}
    )
    assert [item["conversation_id"] for item in with_correction.json()["items"]] == [first_conv.id]
    without_correction = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "has_correction": "false"}
    )
    assert without_correction.json()["total"] == 3

    # 是否已处理：第四期只有未处理。
    unhandled = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "handled": "false"}
    )
    assert unhandled.json()["total"] == 4
    handled = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "handled": "true"}
    )
    assert handled.json()["total"] == 0

    # 时间范围：默认 7 天不含 10 天前的反馈。
    default_range = await client.get(LIST_URL.format(app_id=app_row.id))
    assert default_range.json()["total"] == 3

    # 分页。
    page1 = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "page": 1, "page_size": 2}
    )
    assert page1.json()["total"] == 4
    assert len(page1.json()["items"]) == 2
    page2 = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "page": 2, "page_size": 2}
    )
    assert len(page2.json()["items"]) == 2


async def test_feedback_list_requires_edit_permission(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"反馈权限组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    viewer = await make_user(session, Role.builder, org)
    app_row = await make_app(session, org, owner, visibility="org")

    # 仅有“查看”权限（同组织可见）的人读反馈返回 403。
    login_as(client, viewer)
    forbidden = await client.get(LIST_URL.format(app_id=app_row.id))
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "PERMISSION_DENIED"

    # 应用“编辑”（创建人）可读。
    login_as(client, owner)
    assert (await client.get(LIST_URL.format(app_id=app_row.id))).status_code == 200


async def test_feedback_summary_matches_data(
    app, client: AsyncClient, session: AsyncSession
) -> None:
    org = await make_org(session, f"统计组织-{uuid.uuid4().hex[:6]}")
    owner = await make_user(session, Role.builder, org)
    user = await make_user(session, Role.end_user, org)
    app_row = await make_app(session, org, owner)
    now = datetime.now(UTC)

    rows = [
        ("up", None, now),
        ("up", None, now - timedelta(minutes=1)),
        ("down", "纠错一", now - timedelta(minutes=2)),
        ("down", None, now - timedelta(minutes=3)),
        ("down", "纠错二", now - timedelta(days=10)),
    ]
    for index, (rating, correction, created_at) in enumerate(rows):
        conversation = await make_conversation(session, app_row, user, title=f"会话{index}")
        _, answer = await add_turn(session, conversation, app_row, seq=1)
        await add_feedback(
            session,
            app_row,
            answer,
            user,
            rating,
            correction=correction,
            created_at=created_at,
        )

    login_as(client, owner)
    all_time = await client.get(LIST_URL.format(app_id=app_row.id), params={"range": "all"})
    assert all_time.json()["summary"] == {"up": 2, "down": 3, "down_with_correction": 2}

    # 统计按时间范围，但不随点赞筛选变化。
    default_range = await client.get(LIST_URL.format(app_id=app_row.id))
    assert default_range.json()["summary"] == {"up": 2, "down": 2, "down_with_correction": 1}
    up_only = await client.get(
        LIST_URL.format(app_id=app_row.id), params={"range": "all", "rating": "up"}
    )
    assert up_only.json()["summary"] == {"up": 2, "down": 3, "down_with_correction": 2}


async def test_add_to_qa_not_implemented(client: AsyncClient) -> None:
    """纠错加入问答库属第六期，第四期没有该路由。"""
    response = await client.post("/api/v1/feedback/1:add-to-qa", json={})
    assert response.status_code == 404
