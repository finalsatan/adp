"""创建初始系统管理员的逻辑（供 cli 和测试调用）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UsernameExistsError, WeakPasswordError
from app.core.security import check_password_strength, hash_password
from app.iam.tables import User
from app.settings.service import get_settings


async def create_admin(session: AsyncSession, username: str, password: str) -> User:
    """创建系统管理员；用户名存小写，首次登录强制改密。"""
    normalized = username.strip().lower()

    settings = await get_settings(session)
    reasons = check_password_strength(
        password,
        normalized,
        settings.password.min_length,
        settings.password.min_classes,
    )
    if reasons:
        raise WeakPasswordError(reasons)

    existing = await session.scalar(select(User.id).where(User.username == normalized))
    if existing is not None:
        raise UsernameExistsError()

    user = User(
        username=normalized,
        password_hash=hash_password(password),
        name=normalized,
        role="sys_admin",
        org_id=None,
        must_change_password=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
