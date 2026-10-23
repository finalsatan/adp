"""用量统计接口：汇总、按天趋势、来源列表与权限范围（技术方案 9.7、13.2）。"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import Role
from app.gateway.tables import ModelUsage
from app.models.tables import Model
from app.resources.tables import Resource
from tests.factories import auth_cookies, make_org, make_user

FROM = "2026-10-01T00:00:00Z"
TO = "2026-10-03T00:00:00Z"


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


@pytest_asyncio.fixture
async def admin(session: AsyncSession):
    return await make_user(session, role=Role.sys_admin)


async def _login(client: AsyncClient, user) -> None:
    client.cookies.update(auth_cookies(user))


async def _make_model(
    session: AsyncSession, name: str, created_by: int, *, deleted: bool = False
) -> Model:
    model = Model(
        name=name,
        model_type="chat",
        litellm_model="openai/gpt-4o",
        created_by=created_by,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    session.add(model)
    await session.commit()
    return model


async def _add_usage(
    session: AsyncSession,
    *,
    at: datetime,
    model_id: int | None = None,
    model_name: str = "模型",
    user_id: int | None = None,
    org_id: int | None = None,
    source: str = "playground",
    source_id: int | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    ok: bool = True,
) -> ModelUsage:
    usage = ModelUsage(
        at=at,
        model_id=model_id,
        model_name=model_name,
        user_id=user_id,
        org_id=org_id,
        source=source,
        source_id=source_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        ok=ok,
    )
    session.add(usage)
    await session.commit()
    return usage


async def _summary(client: AsyncClient, *, from_: str = FROM, to: str = TO, **params):
    params.setdefault("group_by", "model")
    return await client.get("/api/v1/usage/summary", params={"from": from_, "to": to, **params})


async def _daily(
    client: AsyncClient, *, from_: str = FROM, to: str = TO, tz: str = "UTC", **params
):
    return await client.get(
        "/api/v1/usage/daily", params={"from": from_, "to": to, "tz": tz, **params}
    )


async def test_summary_by_model_matches_records(
    client: AsyncClient, session: AsyncSession, org_tree, admin
) -> None:
    await _login(client, admin)
    m1 = await _make_model(session, "模型甲", admin.id)
    m2 = await _make_model(session, "模型乙", admin.id)
    await _add_usage(
        session,
        at=_dt(1, 10),
        model_id=m1.id,
        model_name="模型甲",
        org_id=org_tree.rd.id,
        input_tokens=100,
        output_tokens=50,
        ok=True,
    )
    await _add_usage(
        session,
        at=_dt(1, 11),
        model_id=m1.id,
        model_name="模型甲",
        org_id=org_tree.rd.id,
        input_tokens=200,
        output_tokens=0,
        ok=False,
    )
    await _add_usage(
        session,
        at=_dt(2, 10),
        model_id=m2.id,
        model_name="模型乙",
        org_id=org_tree.platform.id,
        input_tokens=30,
        output_tokens=20,
        ok=True,
    )
    await _add_usage(
        session,
        at=_dt(2, 12),
        model_id=None,
        model_name="临时模型",
        org_id=org_tree.rd.id,
        input_tokens=5,
        output_tokens=5,
        ok=True,
    )

    # 分页只影响 items，不影响 totals。
    page = await _summary(client, page_size=1)
    assert page.status_code == 200, page.text
    data = page.json()
    assert data["totals"] == {
        "calls": 4,
        "failures": 1,
        "input_tokens": 335,
        "output_tokens": 75,
    }
    assert data["total"] == 3
    assert len(data["items"]) == 1

    full = (await _summary(client, page_size=10)).json()
    items = {item["label"]: item for item in full["items"]}
    assert items["模型甲"] == {
        "key": str(m1.id),
        "label": "模型甲",
        "sub_label": None,
        "calls": 2,
        "failures": 1,
        "input_tokens": 300,
        "output_tokens": 50,
    }
    assert items["模型乙"]["calls"] == 1
    assert items["未保存的模型"]["key"] is None
    assert items["未保存的模型"]["calls"] == 1

    # 按调用次数倒序，第一条是模型甲。
    ordered = (await _summary(client, page_size=10, sort_by="calls", order="desc")).json()
    assert ordered["items"][0]["label"] == "模型甲"


async def test_summary_by_org_includes_descendants(
    client: AsyncClient, session: AsyncSession, org_tree, admin
) -> None:
    await _login(client, admin)
    await _add_usage(session, at=_dt(1, 10), org_id=org_tree.rd.id, input_tokens=10)
    await _add_usage(session, at=_dt(1, 11), org_id=org_tree.platform.id, input_tokens=20)
    await _add_usage(session, at=_dt(1, 12), org_id=org_tree.product.id, input_tokens=30)
    await _add_usage(session, at=_dt(1, 13), org_id=None, input_tokens=40)

    resp = await _summary(client, group_by="org", page_size=10)
    items = {item["label"]: item for item in resp.json()["items"]}
    assert items["研发中心"]["sub_label"] == "集团下级"
    assert items["平台组"]["sub_label"] == "研发中心下级"
    assert items["系统管理员"]["key"] is None

    # 筛选研发中心：含下级平台组，不含产品中心，也不含系统管理员。
    filtered = (await _summary(client, group_by="org", org_id=org_tree.rd.id, page_size=10)).json()
    assert filtered["total"] == 2
    assert {item["label"] for item in filtered["items"]} == {"研发中心", "平台组"}


async def test_org_snapshot_survives_user_org_change(
    client: AsyncClient, session: AsyncSession, org_tree, admin
) -> None:
    user = await make_user(session, role=Role.builder, org=org_tree.rd)
    await _add_usage(session, at=_dt(1, 10), user_id=user.id, org_id=org_tree.rd.id)

    user.org_id = org_tree.product.id
    await session.commit()

    await _login(client, admin)
    items = {
        item["label"]: item
        for item in (await _summary(client, group_by="org", page_size=10)).json()["items"]
    }
    assert items["研发中心"]["calls"] == 1
    assert "产品中心" not in items


async def test_org_admin_scope_and_forbidden_org(
    client: AsyncClient, session: AsyncSession, org_tree
) -> None:
    org_admin = await make_user(session, role=Role.org_admin, org=org_tree.rd)
    await _add_usage(session, at=_dt(1, 10), org_id=org_tree.rd.id)
    await _add_usage(session, at=_dt(1, 11), org_id=org_tree.platform.id)
    await _add_usage(session, at=_dt(1, 12), org_id=org_tree.product.id)
    await _add_usage(session, at=_dt(1, 13), org_id=None)
    await _login(client, org_admin)

    resp = await _summary(client, group_by="org", page_size=10)
    assert resp.status_code == 200
    assert {item["label"] for item in resp.json()["items"]} == {"研发中心", "平台组"}
    assert resp.json()["totals"]["calls"] == 2

    forbidden = await _summary(client, group_by="org", org_id=org_tree.product.id)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "PERMISSION_DENIED"

    own = await _summary(client, group_by="org", org_id=org_tree.rd.id)
    assert own.status_code == 200
    assert own.json()["total"] == 2


@pytest.mark.parametrize("role", [Role.builder, Role.end_user])
async def test_usage_forbidden_for_non_admin(
    client: AsyncClient, session: AsyncSession, org_tree, role: Role
) -> None:
    user = await make_user(session, role=role, org=org_tree.rd)
    await _login(client, user)
    for path in (
        f"/api/v1/usage/summary?from={FROM}&to={TO}&group_by=model",
        f"/api/v1/usage/daily?from={FROM}&to={TO}&tz=UTC",
        "/api/v1/usage/meta",
    ):
        response = await client.get(path)
        assert response.status_code == 403, path
        assert response.json()["code"] == "PERMISSION_DENIED"


async def test_invalid_time_range(client: AsyncClient, admin) -> None:
    await _login(client, admin)
    over = await _summary(client, from_="2026-01-01T00:00:00Z", to="2027-01-03T00:00:00Z")
    assert over.status_code == 422
    assert over.json()["code"] == "INVALID_TIME_RANGE"

    reversed_ = await _summary(client, from_="2026-01-02T00:00:00Z", to="2026-01-01T00:00:00Z")
    assert reversed_.status_code == 422
    assert reversed_.json()["code"] == "INVALID_TIME_RANGE"

    # 恰好 366 天允许。
    ok = await _summary(client, from_="2026-01-01T00:00:00Z", to="2027-01-02T00:00:00Z")
    assert ok.status_code == 200


async def test_daily_groups_by_timezone(
    client: AsyncClient, session: AsyncSession, org_tree, admin
) -> None:
    await _login(client, admin)
    await _add_usage(
        session,
        at=datetime(2026, 10, 21, 17, 0, tzinfo=UTC),
        org_id=org_tree.rd.id,
        input_tokens=7,
        output_tokens=3,
    )

    utc = (await _daily(client, from_="2026-10-21T00:00:00Z", to="2026-10-22T00:00:00Z")).json()
    assert [(item["date"], item["calls"]) for item in utc["items"]] == [("2026-10-21", 1)]
    assert utc["items"][0]["input_tokens"] == 7
    assert utc["items"][0]["output_tokens"] == 3

    shanghai = (
        await _daily(
            client,
            from_="2026-10-21T00:00:00Z",
            to="2026-10-22T00:00:00Z",
            tz="Asia/Shanghai",
        )
    ).json()
    assert [(item["date"], item["calls"]) for item in shanghai["items"]] == [
        ("2026-10-21", 0),
        ("2026-10-22", 1),
    ]

    # 没有调用的日期补 0。
    filled = (await _daily(client, from_="2026-10-20T00:00:00Z", to="2026-10-23T00:00:00Z")).json()
    assert [(item["date"], item["calls"]) for item in filled["items"]] == [
        ("2026-10-20", 0),
        ("2026-10-21", 1),
        ("2026-10-22", 0),
    ]

    bad_tz = await _daily(client, tz="Not/AZone")
    assert bad_tz.status_code == 422


async def test_deleted_model_and_org_still_named(
    client: AsyncClient, session: AsyncSession, org_tree, admin
) -> None:
    await _login(client, admin)
    model = await _make_model(session, "已删模型", admin.id, deleted=True)
    temp_org = await make_org(session, "临时组织")
    temp_org.deleted_at = datetime.now(UTC)
    await session.commit()

    await _add_usage(
        session,
        at=_dt(1, 10),
        model_id=model.id,
        model_name="已删模型",
        org_id=temp_org.id,
        input_tokens=1,
    )
    await _add_usage(
        session,
        at=_dt(1, 11),
        model_id=None,
        model_name="临时模型",
        org_id=org_tree.rd.id,
        input_tokens=1,
    )

    model_labels = {
        item["label"]
        for item in (await _summary(client, group_by="model", page_size=10)).json()["items"]
    }
    assert {"已删模型", "未保存的模型"} <= model_labels

    org_labels = {
        item["label"]
        for item in (await _summary(client, group_by="org", page_size=10)).json()["items"]
    }
    assert "临时组织" in org_labels


async def test_app_dimension_and_app_id_filter(
    client: AsyncClient, session: AsyncSession, org_tree, admin
) -> None:
    await _login(client, admin)
    app_resource = Resource(
        type="app",
        name="报销助手",
        org_id=org_tree.rd.id,
        owner_id=admin.id,
        visibility="org",
    )
    session.add(app_resource)
    await session.commit()

    await _add_usage(
        session,
        at=_dt(1, 10),
        source="app",
        source_id=app_resource.id,
        org_id=org_tree.rd.id,
        input_tokens=1,
    )
    await _add_usage(
        session,
        at=_dt(1, 11),
        source="app_debug",
        source_id=app_resource.id,
        org_id=org_tree.rd.id,
        input_tokens=1,
    )
    await _add_usage(session, at=_dt(1, 12), source="playground", org_id=org_tree.rd.id)

    app_dim = (await _summary(client, group_by="app", page_size=10)).json()
    assert app_dim["total"] == 1
    assert app_dim["items"][0]["label"] == "报销助手"
    assert app_dim["items"][0]["calls"] == 2

    by_app = (
        await _summary(client, group_by="source", app_id=app_resource.id, page_size=10)
    ).json()
    assert {item["label"] for item in by_app["items"]} == {"应用", "应用调试"}


async def test_usage_meta_lists_sources(client: AsyncClient, admin) -> None:
    await _login(client, admin)
    response = await client.get("/api/v1/usage/meta")
    assert response.status_code == 200
    assert response.json()["sources"] == [
        {"code": "playground", "label": "模型体验"},
        {"code": "model_test", "label": "连通测试"},
        {"code": "app", "label": "应用"},
        {"code": "app_debug", "label": "应用调试"},
        {"code": "ingest", "label": "入库"},
        {"code": "kb_hit_test", "label": "命中测试"},
        {"code": "scheme_trial", "label": "方案试运行"},
        {"code": "api", "label": "API"},
    ]


async def test_daily_without_records_fills_zeros(client: AsyncClient, admin) -> None:
    await _login(client, admin)
    response = await _daily(client, from_="2026-10-01T00:00:00Z", to="2026-10-03T00:00:00Z")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert all(item["calls"] == 0 for item in response.json()["items"])
