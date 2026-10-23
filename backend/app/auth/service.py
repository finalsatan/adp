"""认证业务逻辑：登录（含失败锁定）、刷新令牌、登出、改密。

接口层只负责取参数、调这里的函数、写审计和设置 Cookie。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.lockout import fail_key, lock_key, locked_set_key
from app.auth.tables import RefreshToken
from app.core.errors import (
    AccountDisabledError,
    AppError,
    AuthRequiredError,
    InvalidCredentialsError,
    LoginLockedError,
    RefreshInvalidError,
    ValidationError,
    WeakPasswordError,
    WrongOldPasswordError,
)
from app.core.security import (
    DUMMY_HASH,
    TokenInvalid,
    check_password_strength,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.iam.context import Ctx, bump_perm_version
from app.iam.tables import User
from app.settings import service as settings_service

# 刷新宽限期：旧令牌作废后 30 秒内再用它刷新，返回刚签发的那个新令牌。
GRACE_SECONDS = 30


def grace_key(token_hash: str) -> str:
    return f"rt_grace:{token_hash}"


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    access_minutes: int
    refresh_days: int


@dataclass(frozen=True)
class LoginOutcome:
    """登录结果。失败时 error 非空，reason 供审计记录。"""

    user: User | None
    error: AppError | None = None
    reason: str | None = None
    tokens: TokenBundle | None = None
    must_change_password: bool = False


@dataclass(frozen=True)
class RefreshOutcome:
    error: AppError | None = None
    tokens: TokenBundle | None = None
    must_change_password: bool = False


@dataclass(frozen=True)
class ChangePasswordResult:
    user: User
    tokens: TokenBundle


async def _find_user(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(select(User).where(User.username == username))


def _bundle(user: User, session_id: str, refresh_raw: str, cfg) -> TokenBundle:
    access_token = create_access_token(
        user.id,
        session_id,
        user.token_version,
        user.must_change_password,
        cfg.session.access_minutes,
    )
    return TokenBundle(
        access_token=access_token,
        refresh_token=refresh_raw,
        access_minutes=cfg.session.access_minutes,
        refresh_days=cfg.session.refresh_days,
    )


def _add_refresh_token(
    session: AsyncSession,
    user: User,
    session_id: str,
    refresh_raw: str,
    ip: str | None,
    user_agent: str | None,
    refresh_days: int,
) -> None:
    now = datetime.now(UTC)
    session.add(
        RefreshToken(
            user_id=user.id,
            session_id=session_id,
            token_hash=hash_refresh_token(refresh_raw),
            token_version=user.token_version,
            expires_at=now + timedelta(days=refresh_days),
            ip=ip,
            user_agent=user_agent,
        )
    )


async def _register_failure(
    redis: Redis,
    username: str,
    ip: str,
    cfg,
    user: User | None,
    reason: str,
) -> LoginOutcome:
    """记一次失败；达到阈值时锁定该用户名在该 IP 的登录。"""
    fail = fail_key(username, ip)
    count = await redis.incr(fail)
    if count == 1:
        await redis.expire(fail, cfg.login.lock_minutes * 60)
    if count >= cfg.login.lock_threshold:
        await redis.set(lock_key(username, ip), "1", ex=cfg.login.lock_minutes * 60)
        locked = locked_set_key(username)
        await redis.sadd(locked, ip)
        await redis.expire(locked, cfg.login.lock_minutes * 60)
    return LoginOutcome(user=user, error=InvalidCredentialsError(), reason=reason)


async def login(
    session: AsyncSession,
    redis: Redis,
    username: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> LoginOutcome:
    """登录。用户名已由接口层去空格转小写。"""
    cfg = await settings_service.get_settings(session)
    ip_key = ip or ""

    if await redis.exists(lock_key(username, ip_key)):
        user = await _find_user(session, username)
        return LoginOutcome(
            user=user,
            error=LoginLockedError(cfg.login.lock_minutes),
            reason="locked",
        )

    user = await _find_user(session, username)
    if user is None:
        # 用户不存在也做一次 verify，让耗时与密码错误一致。
        verify_password(DUMMY_HASH, password)
        return await _register_failure(redis, username, ip_key, cfg, None, "no_such_user")

    if not verify_password(user.password_hash, password):
        return await _register_failure(redis, username, ip_key, cfg, user, "wrong_password")

    if user.status != "active":
        # 密码正确但已停用：不计失败次数。
        return LoginOutcome(user=user, error=AccountDisabledError(), reason="disabled")

    await redis.delete(fail_key(username, ip_key))
    session_id = uuid.uuid4().hex
    refresh_raw, _ = new_refresh_token()
    _add_refresh_token(
        session, user, session_id, refresh_raw, ip, user_agent, cfg.session.refresh_days
    )
    user.last_login_at = datetime.now(UTC)
    await session.commit()

    return LoginOutcome(
        user=user,
        tokens=_bundle(user, session_id, refresh_raw, cfg),
        must_change_password=user.must_change_password,
    )


async def revoke_session_tokens(session: AsyncSession, session_id: str) -> None:
    """作废该 session 下全部未作废的刷新令牌。不提交，由调用方提交。"""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def revoke_user_tokens(session: AsyncSession, user_id: int) -> None:
    """作废该用户全部未作废的刷新令牌。不提交，由调用方提交。"""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def refresh(
    session: AsyncSession,
    redis: Redis,
    raw_token: str | None,
    ip: str | None,
    user_agent: str | None,
) -> RefreshOutcome:
    """刷新令牌：轮换、宽限期、盗用检测。"""
    if not raw_token:
        return RefreshOutcome(error=RefreshInvalidError())

    cfg = await settings_service.get_settings(session)
    token_hash = hash_refresh_token(raw_token)
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    if row is None:
        return RefreshOutcome(error=RefreshInvalidError())

    if row.revoked_at is not None:
        # 宽限期内：返回刚签发的那个新令牌，多个标签页同时刷新不互相踢下线。
        cached = await redis.get(grace_key(token_hash))
        if cached is not None:
            user = await session.get(User, row.user_id)
            if user is None or user.status != "active" or user.token_version != row.token_version:
                return RefreshOutcome(error=RefreshInvalidError())
            return RefreshOutcome(
                tokens=_bundle(user, row.session_id, cached, cfg),
                must_change_password=user.must_change_password,
            )
        # 超过宽限期再用旧令牌：视为被盗用，作废该次登录的全部刷新令牌。
        await revoke_session_tokens(session, row.session_id)
        await session.commit()
        return RefreshOutcome(error=RefreshInvalidError())

    now = datetime.now(UTC)
    if row.expires_at <= now:
        return RefreshOutcome(error=RefreshInvalidError())

    user = await session.get(User, row.user_id)
    if user is None or user.status != "active" or user.token_version != row.token_version:
        return RefreshOutcome(error=RefreshInvalidError())

    new_raw, _ = new_refresh_token()
    _add_refresh_token(
        session, user, row.session_id, new_raw, ip, user_agent, cfg.session.refresh_days
    )
    row.revoked_at = now
    # 宽限期缓存必须在提交事务之前写，否则并发请求会看不到。
    await redis.set(grace_key(token_hash), new_raw, ex=GRACE_SECONDS)
    await session.commit()

    return RefreshOutcome(
        tokens=_bundle(user, row.session_id, new_raw, cfg),
        must_change_password=user.must_change_password,
    )


async def logout(session: AsyncSession, access_token: str | None) -> None:
    """按访问令牌的 sid 作废该次登录的全部刷新令牌。签名不对时忽略。"""
    if not access_token:
        return
    try:
        claims = decode_access_token(access_token, verify_exp=False)
    except TokenInvalid:
        return
    await revoke_session_tokens(session, claims.sid)
    await session.commit()


async def change_password(
    session: AsyncSession,
    redis: Redis,
    ctx: Ctx,
    old_password: str | None,
    new_password: str,
    user_agent: str | None,
) -> ChangePasswordResult:
    """改密：更新哈希、token_version + 1、作废该用户全部刷新令牌，并为当前会话换发令牌。"""
    cfg = await settings_service.get_settings(session)
    user = await session.get(User, ctx.user_id)
    if user is None or user.status != "active":
        raise AuthRequiredError()

    if not ctx.pwc:
        if old_password is None:
            raise ValidationError()
        if not verify_password(user.password_hash, old_password):
            raise WrongOldPasswordError()

    reasons = check_password_strength(
        new_password, user.username, cfg.password.min_length, cfg.password.min_classes
    )
    if reasons:
        raise WeakPasswordError(reasons)

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.token_version += 1
    user.updated_at = datetime.now(UTC)

    await revoke_user_tokens(session, user.id)
    new_raw, _ = new_refresh_token()
    _add_refresh_token(
        session, user, ctx.session_id, new_raw, ctx.ip, user_agent, cfg.session.refresh_days
    )
    await session.commit()
    await bump_perm_version(redis)

    return ChangePasswordResult(
        user=user,
        tokens=_bundle(user, ctx.session_id, new_raw, cfg),
    )
