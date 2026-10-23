"""模型密钥不泄露：接口响应、校验错误、审计详情与数据库密文。"""

import json
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.core.schemas import Role
from tests.factories import auth_cookies, make_user

SECRET = "sk-plain-secret-1234567890"
NEW_SECRET = "sk-rotated-secret-0987654321"

CHAT_MODEL: dict[str, Any] = {
    "name": "密钥测试模型",
    "model_type": "chat",
    "litellm_model": "openai/gpt-4o",
    "context_length": 128000,
    "max_output": 4096,
    "api_key": SECRET,
}


@pytest_asyncio.fixture
async def admin_client(client: AsyncClient, session: AsyncSession) -> AsyncClient:
    user = await make_user(session, role=Role.sys_admin)
    client.cookies.update(auth_cookies(user))
    return client


async def test_secret_not_in_any_response(admin_client: AsyncClient, session: AsyncSession) -> None:
    created = await admin_client.post("/api/v1/models", json=CHAT_MODEL)
    assert created.status_code == 201, created.text
    assert SECRET not in created.text
    model = created.json()
    assert model["api_key_last4"] == "7890"

    listed = await admin_client.get("/api/v1/models")
    assert SECRET not in listed.text

    detail = await admin_client.get(f"/api/v1/models/{model['id']}")
    assert SECRET not in detail.text

    updated = await admin_client.patch(
        f"/api/v1/models/{model['id']}", json={"api_key": NEW_SECRET}
    )
    assert updated.status_code == 200
    assert SECRET not in updated.text
    assert NEW_SECRET not in updated.text
    assert updated.json()["api_key_last4"] == "4321"

    renamed = await admin_client.patch(f"/api/v1/models/{model['id']}", json={"name": "改名"})
    assert SECRET not in renamed.text
    assert NEW_SECRET not in renamed.text

    # 校验失败的响应也不能回显密钥。
    invalid = await admin_client.post(
        "/api/v1/models",
        json={
            "name": "缺字段",
            "model_type": "chat",
            "litellm_model": "openai/gpt-4o",
            "api_key": "sk-must-not-leak-0000",
        },
    )
    assert invalid.status_code == 422
    assert "sk-must-not-leak-0000" not in invalid.text


async def test_cipher_stored_encrypted(admin_client: AsyncClient, session: AsyncSession) -> None:
    created = await admin_client.post("/api/v1/models", json=CHAT_MODEL)
    model_id = created.json()["id"]

    row = (
        await session.execute(
            text("SELECT api_key_cipher, api_key_last4 FROM models WHERE id = :id"),
            {"id": model_id},
        )
    ).one()
    cipher, stored_last4 = row
    assert isinstance(cipher, bytes)
    assert cipher != SECRET.encode()
    assert SECRET.encode() not in cipher
    assert decrypt_secret(cipher) == SECRET
    assert stored_last4 == "7890"


async def test_audit_detail_has_no_secret(admin_client: AsyncClient, session: AsyncSession) -> None:
    created = await admin_client.post("/api/v1/models", json=CHAT_MODEL)
    await admin_client.patch(f"/api/v1/models/{created.json()['id']}", json={"api_key": NEW_SECRET})

    rows = (await session.execute(text("SELECT detail FROM audit_logs"))).scalars().all()
    details = json.dumps(list(rows), ensure_ascii=False)
    assert SECRET not in details
    assert NEW_SECRET not in details
