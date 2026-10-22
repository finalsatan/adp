"""管理范围判断（技术方案 7.2）。"""

from sqlalchemy import Select, select

from app.core.schemas import Role
from app.iam.context import Ctx
from app.iam.tables import Org


def in_scope(ctx: Ctx, org_path: str | None) -> bool:
    """目标组织是否在操作人的管理范围内。"""
    if ctx.role == Role.sys_admin:
        return True
    if ctx.role == Role.org_admin and ctx.org_path is not None and org_path is not None:
        return org_path.startswith(ctx.org_path)
    return False


def strictly_below(ctx: Ctx, org_path: str | None) -> bool:
    """目标组织是否严格在操作人组织之下（不含本组织）。"""
    if ctx.role == Role.sys_admin:
        return True
    if ctx.role == Role.org_admin and ctx.org_path is not None and org_path is not None:
        return org_path.startswith(ctx.org_path) and org_path != ctx.org_path
    return False


def scope_org_ids_sql(ctx: Ctx) -> Select | None:
    """管理范围内的组织 id 子查询；系统管理员返回 None 表示不加条件。

    包含已软删除的组织（审计和用量查询要用）。
    """
    if ctx.role == Role.sys_admin or ctx.org_path is None:
        return None
    return select(Org.id).where(Org.path.like(f"{ctx.org_path}%"))


def can_manage_account(
    ctx: Ctx,
    target_role: Role,
    target_org_path: str | None,
    target_user_id: int,
) -> bool:
    """能否管理目标账号（防止接管账号，技术方案 7.2）。"""
    if target_user_id == ctx.user_id:
        return False
    if ctx.role == Role.sys_admin:
        return True
    if ctx.role != Role.org_admin:
        return False
    if target_role == Role.sys_admin:
        return False
    if not in_scope(ctx, target_org_path):
        return False
    if target_role == Role.org_admin and not strictly_below(ctx, target_org_path):
        return False
    return True
