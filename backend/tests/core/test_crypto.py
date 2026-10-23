"""模型密钥加解密（技术方案 9.2、13.2）。"""

import base64
from types import SimpleNamespace

import pytest

from app.core import crypto
from app.core.crypto import SecretDecryptError, decrypt_secret, encrypt_secret, last4

PLAIN = "sk-abcdefghijklmnop1234"
OTHER_KEY = base64.b64encode(b"another-master-key-0123456789ab").decode()


def test_encrypt_then_decrypt() -> None:
    blob = encrypt_secret(PLAIN)
    assert isinstance(blob, bytes)
    assert decrypt_secret(blob) == PLAIN


def test_encrypt_is_randomized() -> None:
    # 同一明文两次加密得到不同密文（随机 nonce）。
    first = encrypt_secret(PLAIN)
    second = encrypt_secret(PLAIN)
    assert first != second
    assert decrypt_secret(first) == decrypt_secret(second) == PLAIN


def test_wrong_master_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    blob = encrypt_secret(PLAIN)
    monkeypatch.setattr(crypto, "get_settings", lambda: SimpleNamespace(MASTER_KEY=OTHER_KEY))
    with pytest.raises(SecretDecryptError):
        decrypt_secret(blob)


def test_tampered_ciphertext_fails() -> None:
    blob = bytearray(encrypt_secret(PLAIN))
    blob[-1] ^= 0x01
    with pytest.raises(SecretDecryptError):
        decrypt_secret(bytes(blob))


def test_unknown_version_fails() -> None:
    blob = bytearray(encrypt_secret(PLAIN))
    blob[0] = 0x02
    with pytest.raises(SecretDecryptError):
        decrypt_secret(bytes(blob))


def test_last4() -> None:
    assert last4("sk-abcdef123456") == "3456"
    assert last4("abcd") == "abcd"
    assert last4("abc") == "•••"
    assert last4("") == ""
