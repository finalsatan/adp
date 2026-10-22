"""初始化系统管理员命令测试。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UsernameExistsError, WeakPasswordError
from app.core.security import verify_password
from app.iam.admin_bootstrap import create_admin


async def test_create_admin(session: AsyncSession) -> None:
    user = await create_admin(session, "  Admin  ", "Str0ng!Pass")
    assert user.username == "admin"
    assert user.role == "sys_admin"
    assert user.org_id is None
    assert user.must_change_password is True
    assert user.status == "active"
    assert user.created_by is None
    assert verify_password(user.password_hash, "Str0ng!Pass")


async def test_create_admin_duplicate_username(session: AsyncSession) -> None:
    await create_admin(session, "admin", "Str0ng!Pass")
    with pytest.raises(UsernameExistsError):
        await create_admin(session, "ADMIN", "Str0ng!Pass")


async def test_create_admin_weak_password(session: AsyncSession) -> None:
    with pytest.raises(WeakPasswordError) as exc:
        await create_admin(session, "admin", "short")
    assert "TOO_SHORT" in exc.value.detail["reasons"]
