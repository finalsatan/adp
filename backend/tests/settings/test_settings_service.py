"""系统设置服务测试。"""

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings.service import get_settings, update_settings


async def test_empty_db_returns_defaults(session: AsyncSession) -> None:
    settings = await get_settings(session)
    assert settings.password.min_length == 8
    assert settings.password.min_classes == 3
    assert settings.login.lock_threshold == 5
    assert settings.login.lock_minutes == 15
    assert settings.session.access_minutes == 15
    assert settings.session.refresh_days == 7
    assert settings.model_call.ingest_rate_ratio == 0.5
    assert settings.model_call.model_host_allowlist == []


async def test_partial_update_keeps_other_fields(session: AsyncSession) -> None:
    before, after = await update_settings(session, {"login": {"lock_threshold": 7}}, actor_id=1)
    assert before.login.lock_threshold == 5
    assert after.login.lock_threshold == 7
    assert after.login.lock_minutes == 15

    settings = await get_settings(session)
    assert settings.login.lock_threshold == 7
    assert settings.login.lock_minutes == 15
    assert settings.password.min_length == 8


async def test_out_of_range_rejected(session: AsyncSession) -> None:
    with pytest.raises(ValidationError):
        await update_settings(session, {"password": {"min_length": 4}}, actor_id=1)


async def test_bad_allowlist_rejected(session: AsyncSession) -> None:
    with pytest.raises(ValidationError):
        await update_settings(
            session,
            {"model_call": {"model_host_allowlist": ["not a host!"]}},
            actor_id=1,
        )


async def test_valid_allowlist_accepted(session: AsyncSession) -> None:
    hosts = ["10.0.0.0/8", "192.168.1.5", "api.example.com", "localhost", "::1"]
    _, after = await update_settings(
        session, {"model_call": {"model_host_allowlist": hosts}}, actor_id=1
    )
    assert after.model_call.model_host_allowlist == hosts


async def test_update_is_visible_immediately(session: AsyncSession) -> None:
    await get_settings(session)  # 先填充缓存
    await update_settings(session, {"session": {"access_minutes": 30}}, actor_id=1)
    settings = await get_settings(session)
    assert settings.session.access_minutes == 30
