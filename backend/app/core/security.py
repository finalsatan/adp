"""安全工具：密码哈希与强度校验、访问令牌、刷新令牌。"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

DATA_DIR = Path(__file__).resolve().parent / "data"
COMMON_PASSWORDS_PATH = DATA_DIR / "common_passwords.txt"

JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()

# 用户名不存在时也做一次 verify，让耗时与用户存在时一致，避免通过响应时间枚举用户名。
DUMMY_HASH = _hasher.hash("adp-dummy-password-for-timing")


class TokenInvalid(Exception):
    """访问令牌签名错误、格式错误或已过期。"""


@dataclass(frozen=True)
class AccessClaims:
    """访问令牌载荷（技术方案 8.1：不放角色和权限）。"""

    sub: str
    sid: str
    tv: int
    pwc: bool
    exp: int
    jti: str

    @property
    def user_id(self) -> int:
        return int(self.sub)


@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    """启动时读一次常见弱密码表，存成小写的 frozenset。"""
    if not COMMON_PASSWORDS_PATH.exists():
        return frozenset()
    with COMMON_PASSWORDS_PATH.open(encoding="utf-8", errors="ignore") as f:
        return frozenset(line.strip().lower() for line in f if line.strip())


def hash_password(plain: str) -> str:
    """用 argon2id 哈希密码。"""
    return _hasher.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    """校验密码；不匹配或哈希格式不对时返回 False，不抛异常。"""
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _character_classes(password: str) -> int:
    classes = 0
    if any(c.isupper() for c in password):
        classes += 1
    if any(c.islower() for c in password):
        classes += 1
    if any(c.isdigit() for c in password):
        classes += 1
    if any(not c.isalnum() for c in password):
        classes += 1
    return classes


def check_password_strength(
    password: str,
    username: str,
    min_length: int,
    min_classes: int,
) -> list[str]:
    """返回不满足的原因代码，空列表表示通过。"""
    reasons: list[str] = []
    if len(password) < min_length:
        reasons.append("TOO_SHORT")
    if _character_classes(password) < min_classes:
        reasons.append("TOO_FEW_CLASSES")
    if password.strip().lower() == username.strip().lower():
        reasons.append("SAME_AS_USERNAME")
    if password.lower() in _common_passwords():
        reasons.append("COMMON_PASSWORD")
    return reasons


def _jwt_secret() -> str:
    return get_settings().JWT_SECRET


def create_access_token(
    user_id: int,
    session_id: str,
    token_version: int,
    must_change_password: bool,
    ttl_minutes: int,
) -> str:
    """签发访问令牌。载荷只有 sub、sid、tv、pwc、exp、jti。"""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "sid": session_id,
        "tv": token_version,
        "pwc": must_change_password,
        "exp": now + timedelta(minutes=ttl_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, verify_exp: bool = True) -> AccessClaims:
    """解析访问令牌；签名错误、格式错误、过期都抛 TokenInvalid。"""
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": verify_exp},
        )
    except jwt.PyJWTError as exc:
        raise TokenInvalid(str(exc)) from exc

    try:
        return AccessClaims(
            sub=str(payload["sub"]),
            sid=str(payload["sid"]),
            tv=int(payload["tv"]),
            pwc=bool(payload["pwc"]),
            exp=int(payload["exp"]),
            jti=str(payload["jti"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenInvalid("令牌缺少必要字段") from exc


def hash_refresh_token(raw: str) -> str:
    """刷新令牌原文的 SHA-256 十六进制。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """返回（原文，SHA-256 十六进制）。"""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)
