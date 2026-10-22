"""管理范围与账号管理边界测试。"""

from app.core.schemas import Role
from app.iam.context import Ctx
from app.iam.scope import can_manage_account, in_scope, strictly_below


def _ctx(role: Role, org_path: str | None = None, user_id: int = 1) -> Ctx:
    return Ctx(
        user_id=user_id,
        username="u",
        name="u",
        role=role,
        status="active",
        org_id=None,
        org_path=org_path,
        org_name=None,
        perms=frozenset(),
        session_id="s",
        pwc=False,
        ip=None,
        request_id=None,
    )


def test_in_scope(org_tree) -> None:
    admin = _ctx(Role.sys_admin)
    assert in_scope(admin, org_tree.platform.path)

    org_admin = _ctx(Role.org_admin, org_tree.rd.path)
    assert in_scope(org_admin, org_tree.rd.path)
    assert in_scope(org_admin, org_tree.platform.path)
    assert not in_scope(org_admin, org_tree.product.path)
    assert not in_scope(org_admin, org_tree.group.path)

    assert not in_scope(_ctx(Role.builder, org_tree.rd.path), org_tree.rd.path)
    assert not in_scope(_ctx(Role.end_user, org_tree.rd.path), org_tree.rd.path)


def test_strictly_below(org_tree) -> None:
    assert strictly_below(_ctx(Role.sys_admin), org_tree.group.path)

    org_admin = _ctx(Role.org_admin, org_tree.rd.path)
    assert not strictly_below(org_admin, org_tree.rd.path)
    assert strictly_below(org_admin, org_tree.platform.path)
    assert not strictly_below(org_admin, org_tree.product.path)

    assert not strictly_below(_ctx(Role.builder, org_tree.rd.path), org_tree.platform.path)


def test_sys_admin_can_manage_others_but_not_self(org_tree) -> None:
    admin = _ctx(Role.sys_admin, user_id=1)
    assert can_manage_account(admin, Role.sys_admin, None, 2)
    assert can_manage_account(admin, Role.org_admin, org_tree.rd.path, 3)
    assert not can_manage_account(admin, Role.org_admin, org_tree.rd.path, 1)


def test_org_admin_can_manage_below(org_tree) -> None:
    ctx = _ctx(Role.org_admin, org_tree.rd.path, user_id=10)

    # 本组织的搭建者、终端用户
    assert can_manage_account(ctx, Role.builder, org_tree.rd.path, 11)
    assert can_manage_account(ctx, Role.end_user, org_tree.rd.path, 12)
    # 下级组织的组织管理员
    assert can_manage_account(ctx, Role.org_admin, org_tree.platform.path, 13)
    # 同级/本组织的组织管理员不能管
    assert not can_manage_account(ctx, Role.org_admin, org_tree.rd.path, 14)
    # 上级组织的组织管理员不能管
    assert not can_manage_account(ctx, Role.org_admin, org_tree.group.path, 15)
    # 其他分支
    assert not can_manage_account(ctx, Role.builder, org_tree.product.path, 16)
    # 系统管理员不能管
    assert not can_manage_account(ctx, Role.sys_admin, None, 17)
    # 不能管自己
    assert not can_manage_account(ctx, Role.builder, org_tree.rd.path, 10)


def test_non_admin_cannot_manage(org_tree) -> None:
    for role in (Role.builder, Role.end_user):
        ctx = _ctx(role, org_tree.rd.path, user_id=20)
        assert not can_manage_account(ctx, Role.builder, org_tree.rd.path, 21)
        assert not can_manage_account(ctx, Role.end_user, org_tree.platform.path, 22)
