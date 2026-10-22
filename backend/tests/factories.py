"""测试造数据工具：组织、用户、访问令牌 Cookie 与常用组织树。"""

import uuid
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.core.security import create_access_token, hash_password
from app.iam.tables import Org, User

DEFAULT_PASSWORD = "Test@1234x"


async def make_org(session: AsyncSession, name: str, parent: Org | None = None) -> Org:
    """按 P1-04 的方式生成物化路径：先取 id，再拼 path 插入。"""
    org_id = await session.scalar(text("SELECT nextval('orgs_id_seq')"))
    assert org_id is not None
    path = f"{parent.path if parent is not None else '/'}{org_id}/"
    org = Org(
        id=org_id,
        parent_id=parent.id if parent is not None else None,
        path=path,
        name=name,
    )
    session.add(org)
    await session.commit()
    return org


async def make_user(
    session: AsyncSession,
    role: Role,
    org: Org | None = None,
    username: str | None = None,
    password: str = DEFAULT_PASSWORD,
    status: str = "active",
    must_change_password: bool = False,
) -> User:
    name = username or f"u{uuid.uuid4().hex[:10]}"
    user = User(
        username=name.strip().lower(),
        password_hash=hash_password(password),
        name=name,
        role=role,
        org_id=org.id if org is not None else None,
        status=status,
        must_change_password=must_change_password,
    )
    session.add(user)
    await session.commit()
    return user


def auth_cookies(user: User, pwc: bool = False) -> dict[str, str]:
    """直接用 create_access_token 签发访问令牌，返回可传给 httpx 的 cookies。"""
    token = create_access_token(
        user_id=user.id,
        session_id=uuid.uuid4().hex,
        token_version=user.token_version,
        must_change_password=pwc,
        ttl_minutes=15,
    )
    return {"adp_at": token}


@pytest_asyncio.fixture
async def org_tree(session: AsyncSession) -> SimpleNamespace:
    """常用组织树：集团 → 研发中心 →（平台组、算法组），集团 → 产品中心。"""
    group = await make_org(session, "集团")
    rd = await make_org(session, "研发中心", parent=group)
    platform = await make_org(session, "平台组", parent=rd)
    algo = await make_org(session, "算法组", parent=rd)
    product = await make_org(session, "产品中心", parent=group)
    return SimpleNamespace(
        group=group,
        rd=rd,
        platform=platform,
        algo=algo,
        product=product,
    )
