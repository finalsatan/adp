"""用户管理业务逻辑：列表、新建、详情、编辑、启停、重置、解锁、任命与个人资料。

路由只做参数接收、权限依赖、调用这里的函数和写审计。
"""

from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import lockout
from app.auth.lockout import locked_set_key
from app.auth.service import revoke_user_tokens
from app.core.errors import (
    AuthRequiredError,
    LastSysAdminError,
    NotFoundError,
    PermissionDeniedError,
    UsernameExistsError,
    ValidationError,
    WeakPasswordError,
)
from app.core.schemas import OrgBrief, Page, Pagination, Role, UserBrief, UserStatus
from app.core.security import check_password_strength, hash_password
from app.iam import service as iam_service
from app.iam.context import Ctx, bump_perm_version
from app.iam.schemas import MeOut, MePatch, UserCreate, UserOut, UserPatch, UserSearchOut
from app.iam.scope import can_manage_account, in_scope, strictly_below
from app.iam.tables import Org, User
from app.resources.tables import Resource
from app.settings import service as settings_service

# 审计事件：(action, detail)，一次编辑可能产生多条。
AuditEvent = tuple[str, dict | None]


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _load_org(session: AsyncSession, org_id: int) -> Org:
    org = await session.get(Org, org_id)
    if org is None or org.deleted_at is not None:
        raise NotFoundError()
    return org


async def _load_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError()
    return user


async def _user_org(session: AsyncSession, user: User) -> Org | None:
    if user.org_id is None:
        return None
    return await session.get(Org, user.org_id)


async def _owned_counts(session: AsyncSession, users: list[User]) -> dict[int, int]:
    """各用户“当前组织内、由他创建、未删除”的资源数。"""
    ids = [user.id for user in users]
    if not ids:
        return {}
    result = await session.execute(
        select(Resource.owner_id, func.count())
        .join(User, User.id == Resource.owner_id)
        .where(
            Resource.owner_id.in_(ids),
            Resource.deleted_at.is_(None),
            Resource.org_id == User.org_id,
        )
        .group_by(Resource.owner_id)
    )
    return {owner_id: int(count) for owner_id, count in result.all()}


async def _creator_briefs(session: AsyncSession, users: list[User]) -> dict[int, UserBrief]:
    ids = {user.created_by for user in users if user.created_by is not None}
    if not ids:
        return {}
    rows = (
        await session.execute(select(User.id, User.username, User.name).where(User.id.in_(ids)))
    ).all()
    return {
        user_id: UserBrief(id=user_id, username=username, name=name)
        for user_id, username, name in rows
    }


async def _locked_usernames(redis: Redis, usernames: list[str]) -> set[str]:
    """一次 pipeline 查完一页用户的锁定状态。"""
    if not usernames:
        return set()
    pipe = redis.pipeline()
    for username in usernames:
        pipe.scard(locked_set_key(username))
    counts = await pipe.execute()
    return {username for username, count in zip(usernames, counts, strict=True) if count}


def _build_user_out(
    ctx: Ctx,
    user: User,
    org: Org | None,
    creator: UserBrief | None,
    owned_count: int,
    locked: bool,
) -> UserOut:
    role = Role(user.role)
    return UserOut(
        id=user.id,
        username=user.username,
        name=user.name,
        email=user.email,
        phone=user.phone,
        remark=user.remark,
        org=OrgBrief(id=org.id, name=org.name) if org is not None else None,
        role=role,
        status=UserStatus(user.status),
        locked=locked,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        created_by=creator,
        owned_resource_count=owned_count,
        manageable=can_manage_account(ctx, role, org.path if org is not None else None, user.id),
    )


async def _single_user_out(
    session: AsyncSession, redis: Redis, ctx: Ctx, user: User, org: Org | None = None
) -> UserOut:
    if org is None:
        org = await _user_org(session, user)
    creators = await _creator_briefs(session, [user])
    owned = (await _owned_counts(session, [user])).get(user.id, 0)
    locked = await lockout.is_locked(redis, user.username)
    return _build_user_out(
        ctx, user, org, creators.get(user.created_by) if user.created_by else None, owned, locked
    )


async def list_users(
    session: AsyncSession,
    redis: Redis,
    ctx: Ctx,
    pagination: Pagination,
    org_id: int | None,
    include_sub: bool,
    role: Role | None,
    status: UserStatus | None,
    keyword: str | None,
) -> Page[UserOut]:
    stmt = select(User, Org).join(Org, User.org_id == Org.id, isouter=True)

    if ctx.role != Role.sys_admin:
        if ctx.org_path is None:
            return Page(items=[], total=0)
        stmt = stmt.where(User.org_id.in_(select(Org.id).where(Org.path.like(f"{ctx.org_path}%"))))

    if org_id is not None:
        org = await _load_org(session, org_id)
        if not in_scope(ctx, org.path):
            raise PermissionDeniedError()
        if include_sub:
            stmt = stmt.where(User.org_id.in_(select(Org.id).where(Org.path.like(f"{org.path}%"))))
        else:
            stmt = stmt.where(User.org_id == org.id)

    if role is not None:
        stmt = stmt.where(User.role == role.value)
    if status is not None:
        stmt = stmt.where(User.status == status.value)
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        stmt = stmt.where(
            User.username.ilike(pattern, escape="\\")
            | User.name.ilike(pattern, escape="\\")
            | User.email.ilike(pattern, escape="\\")
        )

    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await session.execute(
            stmt.order_by(User.username)
            .offset((pagination.page - 1) * pagination.page_size)
            .limit(pagination.page_size)
        )
    ).all()

    users = [row[0] for row in rows]
    orgs = {row[1].id: row[1] for row in rows if row[1] is not None}
    creators = await _creator_briefs(session, users)
    owned = await _owned_counts(session, users)
    locked = await _locked_usernames(redis, [user.username for user in users])

    items = [
        _build_user_out(
            ctx,
            user,
            orgs.get(user.org_id),
            creators.get(user.created_by) if user.created_by else None,
            owned.get(user.id, 0),
            user.username in locked,
        )
        for user in users
    ]
    return Page(items=items, total=total)


async def create_user(session: AsyncSession, redis: Redis, ctx: Ctx, body: UserCreate) -> UserOut:
    username = body.username.strip().lower()
    if await session.scalar(select(User.id).where(User.username == username)) is not None:
        raise UsernameExistsError()

    org = await _load_org(session, body.org_id)
    if not in_scope(ctx, org.path):
        raise PermissionDeniedError()

    role = Role(body.role)
    if role == Role.org_admin and ctx.role == Role.org_admin and not strictly_below(ctx, org.path):
        raise PermissionDeniedError()

    cfg = await settings_service.get_settings(session)
    reasons = check_password_strength(
        body.password, username, cfg.password.min_length, cfg.password.min_classes
    )
    if reasons:
        raise WeakPasswordError(reasons)

    user = User(
        username=username,
        password_hash=hash_password(body.password),
        name=body.name,
        email=body.email,
        phone=body.phone,
        remark=body.remark,
        role=role.value,
        org_id=org.id,
        status=UserStatus.active.value,
        must_change_password=True,
        created_by=ctx.user_id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return await _single_user_out(session, redis, ctx, user, org=org)


async def get_user(session: AsyncSession, redis: Redis, ctx: Ctx, user_id: int) -> UserOut:
    user = await _load_user(session, user_id)
    org = await _user_org(session, user)
    if not in_scope(ctx, org.path if org is not None else None):
        raise NotFoundError()
    return await _single_user_out(session, redis, ctx, user, org=org)


def _check_assignable_final_org(ctx: Ctx, role: Role, org: Org | None) -> None:
    """最终角色是组织管理员时，组织管理员只能任命严格下级。"""
    if role == Role.org_admin and ctx.role == Role.org_admin:
        if not strictly_below(ctx, org.path if org is not None else None):
            raise PermissionDeniedError()


async def patch_user(
    session: AsyncSession, redis: Redis, ctx: Ctx, user_id: int, body: UserPatch
) -> tuple[UserOut, list[AuditEvent]]:
    user = await _load_user(session, user_id)
    org = await _user_org(session, user)
    if not can_manage_account(ctx, Role(user.role), org.path if org is not None else None, user.id):
        raise PermissionDeniedError()

    fields_set = body.model_fields_set
    if user.role == Role.sys_admin.value and ({"org_id", "role"} & fields_set):
        raise ValidationError("系统管理员的所属组织与角色请用撤销接口修改")
    if "org_id" in fields_set and body.org_id is None:
        raise ValidationError("所属组织不能为空")

    events: list[AuditEvent] = []

    changes: dict[str, list] = {}
    for field in ("name", "email", "phone", "remark"):
        if field in fields_set:
            new_value = getattr(body, field)
            old_value = getattr(user, field)
            if new_value != old_value:
                changes[field] = [old_value, new_value]
    if changes:
        events.append(("user.update", changes))
        for field, (_, new_value) in changes.items():
            setattr(user, field, new_value)

    final_org = org
    org_changed = False
    if "org_id" in fields_set and body.org_id is not None:
        new_org = await _load_org(session, body.org_id)
        if not in_scope(ctx, new_org.path):
            raise PermissionDeniedError()
        if new_org.id != user.org_id:
            org_changed = True
            final_org = new_org

    role_changed = False
    final_role = Role(user.role)
    if "role" in fields_set and body.role is not None:
        candidate = Role(body.role)
        if candidate != final_role:
            role_changed = True
            final_role = candidate

    _check_assignable_final_org(ctx, final_role, final_org)

    if role_changed:
        events.append(("user.change_role", {"from": user.role, "to": final_role.value}))
        user.role = final_role.value
    if org_changed:
        events.append(
            (
                "user.change_org",
                {
                    "from": {"id": org.id, "name": org.name} if org is not None else None,
                    "to": {"id": final_org.id, "name": final_org.name}
                    if final_org is not None
                    else None,
                },
            )
        )
        user.org_id = final_org.id if final_org is not None else None

    if not events:
        events.append(("user.update", None))

    if role_changed or org_changed or changes:
        user.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(user)
    if role_changed or org_changed:
        await bump_perm_version(redis)

    return await _single_user_out(session, redis, ctx, user, org=final_org), events


async def _lock_sys_admins(session: AsyncSession) -> list[User]:
    """锁住全部系统管理员行，防止并发互相撤销。"""
    result = await session.execute(
        select(User).where(User.role == Role.sys_admin.value).with_for_update()
    )
    return list(result.scalars().all())


async def disable_user(session: AsyncSession, redis: Redis, ctx: Ctx, user_id: int) -> UserOut:
    user = await _load_user(session, user_id)
    org = await _user_org(session, user)
    if not can_manage_account(ctx, Role(user.role), org.path if org is not None else None, user.id):
        raise PermissionDeniedError()

    if user.status == UserStatus.disabled.value:
        return await _single_user_out(session, redis, ctx, user, org=org)

    if user.role == Role.sys_admin.value:
        admins = await _lock_sys_admins(session)
        active = [admin for admin in admins if admin.status == UserStatus.active.value]
        if len(active) <= 1:
            raise LastSysAdminError()

    user.status = UserStatus.disabled.value
    user.token_version += 1
    user.updated_at = datetime.now(UTC)
    await revoke_user_tokens(session, user.id)
    await session.commit()
    await bump_perm_version(redis)
    return await _single_user_out(session, redis, ctx, user, org=org)


async def enable_user(session: AsyncSession, redis: Redis, ctx: Ctx, user_id: int) -> UserOut:
    user = await _load_user(session, user_id)
    org = await _user_org(session, user)
    if not can_manage_account(ctx, Role(user.role), org.path if org is not None else None, user.id):
        raise PermissionDeniedError()

    if user.status == UserStatus.active.value:
        return await _single_user_out(session, redis, ctx, user, org=org)

    user.status = UserStatus.active.value
    user.updated_at = datetime.now(UTC)
    await session.commit()
    await bump_perm_version(redis)
    return await _single_user_out(session, redis, ctx, user, org=org)


async def reset_password(
    session: AsyncSession, redis: Redis, ctx: Ctx, user_id: int, new_password: str
) -> User:
    user = await _load_user(session, user_id)
    org = await _user_org(session, user)
    if not can_manage_account(ctx, Role(user.role), org.path if org is not None else None, user.id):
        raise PermissionDeniedError()

    cfg = await settings_service.get_settings(session)
    reasons = check_password_strength(
        new_password, user.username, cfg.password.min_length, cfg.password.min_classes
    )
    if reasons:
        raise WeakPasswordError(reasons)

    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    user.token_version += 1
    user.updated_at = datetime.now(UTC)
    await revoke_user_tokens(session, user.id)
    await session.commit()
    await bump_perm_version(redis)
    return user


async def unlock_user(session: AsyncSession, redis: Redis, ctx: Ctx, user_id: int) -> User:
    user = await _load_user(session, user_id)
    org = await _user_org(session, user)
    if not can_manage_account(ctx, Role(user.role), org.path if org is not None else None, user.id):
        raise PermissionDeniedError()

    await lockout.unlock(redis, user.username)
    return user


async def grant_sys_admin(
    session: AsyncSession, redis: Redis, ctx: Ctx, user_id: int
) -> tuple[UserOut, int | None]:
    if ctx.role != Role.sys_admin:
        raise PermissionDeniedError()

    user = await _load_user(session, user_id)
    if user.role == Role.sys_admin.value or user.status != UserStatus.active.value:
        raise ValidationError("只能任命启用的非系统管理员")

    original_org_id = user.org_id
    user.role = Role.sys_admin.value
    user.org_id = None
    user.updated_at = datetime.now(UTC)
    await session.commit()
    await bump_perm_version(redis)
    return await _single_user_out(session, redis, ctx, user), original_org_id


async def revoke_sys_admin(
    session: AsyncSession, redis: Redis, ctx: Ctx, user_id: int, org_id: int, role: Role
) -> UserOut:
    if ctx.role != Role.sys_admin:
        raise PermissionDeniedError()

    user = await _load_user(session, user_id)
    if user.role != Role.sys_admin.value:
        raise ValidationError("目标不是系统管理员")

    admins = await _lock_sys_admins(session)
    others = [
        admin for admin in admins if admin.id != user.id and admin.status == UserStatus.active.value
    ]
    if not others:
        raise LastSysAdminError()

    org = await _load_org(session, org_id)
    user.role = role.value
    user.org_id = org.id
    user.updated_at = datetime.now(UTC)
    await session.commit()
    await bump_perm_version(redis)
    return await _single_user_out(session, redis, ctx, user, org=org)


async def update_me(session: AsyncSession, ctx: Ctx, body: MePatch) -> tuple[MeOut, dict]:
    user = await session.get(User, ctx.user_id)
    if user is None or user.status != UserStatus.active.value:
        raise AuthRequiredError()

    changes: dict[str, list] = {}
    for field in ("name", "email", "phone"):
        if field in body.model_fields_set:
            new_value = getattr(body, field)
            old_value = getattr(user, field)
            if new_value != old_value:
                changes[field] = [old_value, new_value]
                setattr(user, field, new_value)
    if changes:
        user.updated_at = datetime.now(UTC)
        await session.commit()

    return await iam_service.get_me(session, ctx), changes


async def search_users(session: AsyncSession, query: str) -> list[UserSearchOut]:
    pattern = _escape_like(query.strip())
    rows = (
        await session.execute(
            select(User, Org)
            .join(Org, User.org_id == Org.id, isouter=True)
            .where(
                User.status == UserStatus.active.value,
                User.username.ilike(f"{pattern}%", escape="\\")
                | User.name.ilike(f"%{pattern}%", escape="\\"),
            )
            .order_by(User.username)
            .limit(20)
        )
    ).all()
    return [
        UserSearchOut(
            id=user.id,
            username=user.username,
            name=user.name,
            org=OrgBrief(id=org.id, name=org.name) if org is not None else None,
        )
        for user, org in rows
    ]
