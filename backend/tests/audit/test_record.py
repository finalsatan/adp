"""审计写入测试：临时挂一组带 audited 的路由。"""

import logging

import pytest_asyncio
from fastapi import APIRouter, Depends, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.audit import service as audit_service
from app.audit.deps import audit_success, audited
from app.audit.service import AuditTarget
from app.audit.tables import AuditLog
from app.core.errors import NameConflictError
from app.core.schemas import Role
from app.iam.deps import CurrentCtx, require_perm
from app.main import create_app
from tests.factories import auth_cookies, make_user

probe_router = APIRouter()


@probe_router.post(
    "/test/audit/ok",
    dependencies=[Depends(audited("user.create"))],
)
async def audit_ok(
    request: Request,
    body: dict,
    ctx: CurrentCtx,
) -> dict:
    await audit_success(
        request,
        AuditTarget("user", 42, "用户 chenjiu", body.get("target_org_id")),
    )
    return {"ok": True}


@probe_router.post(
    "/test/audit/perm-denied",
    dependencies=[
        Depends(audited("user.create")),
        Depends(require_perm("settings.manage")),
    ],
)
async def audit_perm_denied() -> dict:
    return {"ok": True}


@probe_router.post(
    "/test/audit/business-error",
    dependencies=[Depends(audited("user.create"))],
)
async def audit_business_error(ctx: CurrentCtx) -> dict:
    raise NameConflictError()


@probe_router.post(
    "/test/audit/rollback",
    dependencies=[Depends(audited("user.create"))],
)
async def audit_rollback(ctx: CurrentCtx) -> dict:
    # 提交前抛错，audit_success 不会被调用
    raise NameConflictError()


@probe_router.post(
    "/test/audit/detail",
    dependencies=[Depends(audited("user.create"))],
)
async def audit_detail(request: Request, ctx: CurrentCtx) -> dict:
    await audit_success(
        request,
        None,
        {
            "keep": 1,
            "password": "p",
            "new_password": "np",
            "api_key": "k",
            "token": "t",
            "secret": "s",
            "nested": {"password": "np2", "ok": 2},
        },
    )
    return {"ok": True}


@pytest_asyncio.fixture(scope="session")
async def audit_app(settings):
    application = create_app(settings)
    application.include_router(probe_router)
    yield application
    await application.state.redis.aclose()
    await application.state.engine.dispose()


@pytest_asyncio.fixture
async def audit_client(audit_app) -> AsyncClient:
    transport = ASGITransport(app=audit_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _login(client: AsyncClient, user) -> None:
    client.cookies.set("adp_at", auth_cookies(user)["adp_at"])


async def _rows(session) -> list[AuditLog]:
    return list((await session.scalars(select(AuditLog).order_by(AuditLog.id))).all())


async def test_success_record_fields(audit_client: AsyncClient, session, org_tree) -> None:
    actor_org = org_tree.rd
    admin = await make_user(session, role=Role.org_admin, org=actor_org, username="rdadmin")
    target_org = org_tree.algo
    _login(audit_client, admin)

    response = await audit_client.post(
        "/test/audit/ok",
        json={"target_org_id": target_org.id},
        headers={"X-Real-IP": "10.2.3.18", "X-Request-Id": "req-123"},
    )
    assert response.status_code == 200

    rows = await _rows(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_id == admin.id
    assert row.actor_name == "rdadmin"
    assert row.actor_org_id == actor_org.id
    assert row.actor_org_name == "研发中心"
    assert row.action == "user.create"
    assert row.target_type == "user"
    assert row.target_id == 42
    assert row.target_desc == "用户 chenjiu"
    assert row.target_org_id == target_org.id
    assert row.target_org_name == "算法组"
    assert row.result == "success"
    assert row.ip == "10.2.3.18"
    assert row.request_id == "req-123"


async def test_permission_denied_records_failure(
    audit_client: AsyncClient, session, org_tree
) -> None:
    org = org_tree.rd
    builder = await make_user(session, role=Role.builder, org=org, username="rdbuilder")
    _login(audit_client, builder)

    response = await audit_client.post("/test/audit/perm-denied")
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"

    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0].result == "failure"
    assert rows[0].action == "user.create"
    assert rows[0].detail == {"code": "PERMISSION_DENIED"}
    assert rows[0].actor_id == builder.id


async def test_business_error_records_failure(audit_client: AsyncClient, session) -> None:
    admin = await make_user(session, role=Role.sys_admin, username="root")
    _login(audit_client, admin)

    response = await audit_client.post("/test/audit/business-error")
    assert response.status_code == 409

    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0].result == "failure"
    assert rows[0].detail == {"code": "NAME_CONFLICT"}
    assert rows[0].actor_id == admin.id


async def test_unauthenticated_not_recorded(audit_client: AsyncClient, session) -> None:
    response = await audit_client.post("/test/audit/business-error")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"
    assert await _rows(session) == []


async def test_no_success_record_when_route_raises(audit_client: AsyncClient, session) -> None:
    admin = await make_user(session, role=Role.sys_admin, username="root")
    _login(audit_client, admin)

    response = await audit_client.post("/test/audit/rollback")
    assert response.status_code == 409

    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0].result == "failure"


async def test_write_failure_retries_once(
    audit_client: AsyncClient, session, monkeypatch, caplog
) -> None:
    admin = await make_user(session, role=Role.sys_admin, username="root")
    _login(audit_client, admin)

    calls: list[dict] = []

    async def boom(fields: dict) -> None:
        calls.append(fields)
        raise RuntimeError("db down")

    monkeypatch.setattr(audit_service, "_insert", boom)
    with caplog.at_level(logging.ERROR, logger="app.audit.service"):
        response = await audit_client.post("/test/audit/ok", json={})

    assert response.status_code == 200
    assert len(calls) == 2
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert await _rows(session) == []


async def test_detail_sensitive_keys_removed(audit_client: AsyncClient, session) -> None:
    admin = await make_user(session, role=Role.sys_admin, username="root")
    _login(audit_client, admin)

    response = await audit_client.post("/test/audit/detail")
    assert response.status_code == 200

    rows = await _rows(session)
    detail = rows[0].detail
    assert detail["keep"] == 1
    for key in ("password", "new_password", "api_key", "token", "secret"):
        assert key not in detail
    assert detail["nested"] == {"ok": 2}
