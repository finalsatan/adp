"""第一期表结构：唯一约束、check 约束、部分索引和审计表权限。"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession


async def _insert_org(session: AsyncSession, name: str, parent_id: int | None = None) -> int:
    path = "/" if parent_id is None else f"/{parent_id}/"
    result = await session.execute(
        text(
            "INSERT INTO orgs (parent_id, path, name) "
            "VALUES (:parent_id, :path, :name) RETURNING id"
        ),
        {"parent_id": parent_id, "path": path, "name": name},
    )
    await session.commit()
    return result.scalar_one()


async def _insert_user(
    session: AsyncSession, username: str, org_id: int | None, role: str = "builder"
) -> int:
    result = await session.execute(
        text(
            "INSERT INTO users (username, password_hash, name, role, org_id) "
            "VALUES (:username, 'hash', :username, :role, :org_id) RETURNING id"
        ),
        {"username": username, "role": role, "org_id": org_id},
    )
    await session.commit()
    return result.scalar_one()


async def _insert_resource(
    session: AsyncSession, name: str, org_id: int, owner_id: int, type_: str = "kb"
) -> int:
    result = await session.execute(
        text(
            "INSERT INTO resources (type, name, org_id, owner_id, visibility) "
            "VALUES (:type, :name, :org_id, :owner_id, 'org') RETURNING id"
        ),
        {"type": type_, "name": name, "org_id": org_id, "owner_id": owner_id},
    )
    await session.commit()
    return result.scalar_one()


async def test_audit_logs_append_only(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO audit_logs (actor_name, action, result) "
            "VALUES ('alice', 'user.create', 'success')"
        )
    )
    await session.commit()
    assert (
        await session.execute(text("SELECT actor_name FROM audit_logs"))
    ).scalar_one() == "alice"

    with pytest.raises(ProgrammingError):
        await session.execute(text("UPDATE audit_logs SET actor_name = 'bob'"))
    await session.rollback()

    with pytest.raises(ProgrammingError):
        await session.execute(text("DELETE FROM audit_logs"))
    await session.rollback()


async def test_org_name_unique_per_parent(session: AsyncSession) -> None:
    # 两个同名顶级组织
    await _insert_org(session, "总部")
    with pytest.raises(IntegrityError):
        await _insert_org(session, "总部")
    await session.rollback()

    parent = (await session.execute(text("SELECT id FROM orgs WHERE name = '总部'"))).scalar_one()

    # 同一上级下重名
    await _insert_org(session, "研发", parent_id=parent)
    with pytest.raises(IntegrityError):
        await _insert_org(session, "研发", parent_id=parent)
    await session.rollback()

    # 不同上级下可以重名
    other = await _insert_org(session, "分部")
    await _insert_org(session, "研发", parent_id=other)

    # 软删除后可以再建同名
    child = (
        await session.execute(
            text("SELECT id FROM orgs WHERE parent_id = :parent_id AND name = '研发'"),
            {"parent_id": parent},
        )
    ).scalar_one()
    await session.execute(text("UPDATE orgs SET deleted_at = now() WHERE id = :id"), {"id": child})
    await session.commit()
    await _insert_org(session, "研发", parent_id=parent)


async def test_username_unique(session: AsyncSession) -> None:
    org = await _insert_org(session, "总部")
    await _insert_user(session, "alice", org)
    with pytest.raises(IntegrityError):
        await _insert_user(session, "alice", org)
    await session.rollback()


async def test_user_role_org_check(session: AsyncSession) -> None:
    org = await _insert_org(session, "总部")

    # 系统管理员不能属于组织
    with pytest.raises(IntegrityError):
        await _insert_user(session, "admin", org, role="sys_admin")
    await session.rollback()

    # 非系统管理员必须属于一个组织
    with pytest.raises(IntegrityError):
        await _insert_user(session, "builder", None, role="builder")
    await session.rollback()


async def test_resource_name_unique_per_org(session: AsyncSession) -> None:
    org_a = await _insert_org(session, "A")
    org_b = await _insert_org(session, "B")
    user = await _insert_user(session, "alice", org_a)

    await _insert_resource(session, "知识库", org_a, user)
    with pytest.raises(IntegrityError):
        await _insert_resource(session, "知识库", org_a, user)
    await session.rollback()

    # 不同组织可以同名
    await _insert_resource(session, "知识库", org_b, user)

    # 软删除后可以再建同名
    await session.execute(
        text("UPDATE resources SET deleted_at = now() WHERE org_id = :org_id"),
        {"org_id": org_a},
    )
    await session.commit()
    await _insert_resource(session, "知识库", org_a, user)


async def test_app_account_can_use_business_tables(session: AsyncSession) -> None:
    org = await _insert_org(session, "总部")
    user = await _insert_user(session, "alice", org)
    resource = await _insert_resource(session, "知识库", org, user)

    await session.execute(text("UPDATE users SET name = 'Alice' WHERE id = :id"), {"id": user})
    await session.execute(
        text(
            "INSERT INTO resource_members (resource_id, user_id, added_by) "
            "VALUES (:resource_id, :user_id, :user_id)"
        ),
        {"resource_id": resource, "user_id": user},
    )
    await session.execute(
        text("DELETE FROM resource_members WHERE resource_id = :id"), {"id": resource}
    )
    await session.commit()

    assert (await session.execute(text("SELECT name FROM users"))).scalar_one() == "Alice"
