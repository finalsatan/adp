"""安全工具测试。"""

import pytest

from app.core.security import (
    TokenInvalid,
    check_password_strength,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)


def test_hash_and_verify() -> None:
    hashed = hash_password("Str0ng!Pass")
    assert hashed != "Str0ng!Pass"
    assert verify_password(hashed, "Str0ng!Pass")
    assert not verify_password(hashed, "wrong-password")


def test_verify_password_invalid_hash_returns_false() -> None:
    assert not verify_password("not-a-valid-hash", "Str0ng!Pass")


def test_password_strength_passes() -> None:
    assert check_password_strength("Str0ng!Pass", "admin", 8, 3) == []


def test_password_too_short() -> None:
    assert check_password_strength("Ab1!", "admin", 8, 3) == ["TOO_SHORT"]


def test_password_too_few_classes() -> None:
    assert check_password_strength("Aabcdefgh", "admin", 8, 3) == ["TOO_FEW_CLASSES"]


def test_password_same_as_username_ignores_case_and_spaces() -> None:
    assert check_password_strength("Alice.Wang", "alice.wang", 4, 1) == ["SAME_AS_USERNAME"]
    assert check_password_strength("  admin  ", "admin", 4, 1) == ["SAME_AS_USERNAME"]


def test_password_in_common_list_ignores_case() -> None:
    assert check_password_strength("Password1", "admin", 4, 1) == ["COMMON_PASSWORD"]


def test_access_token_round_trip() -> None:
    token = create_access_token(7, "session-abc", 3, True, 15)
    claims = decode_access_token(token)
    assert claims.sub == "7"
    assert claims.user_id == 7
    assert claims.sid == "session-abc"
    assert claims.tv == 3
    assert claims.pwc is True
    assert claims.jti


def test_tampered_access_token() -> None:
    token = create_access_token(1, "s", 0, False, 15)
    tampered = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
    with pytest.raises(TokenInvalid):
        decode_access_token(tampered)


def test_expired_access_token() -> None:
    token = create_access_token(1, "s", 0, False, -1)
    with pytest.raises(TokenInvalid):
        decode_access_token(token)
    claims = decode_access_token(token, verify_exp=False)
    assert claims.sid == "s"


def test_refresh_token_hash_matches() -> None:
    raw, hashed = new_refresh_token()
    assert hash_refresh_token(raw) == hashed
    assert len(hashed) == 64
    other, _ = new_refresh_token()
    assert other != raw
