"""模型密钥的加解密（技术方案 9.2）。

主密钥来自环境变量 MASTER_KEY（Base64 编码的 32 字节）。每个密钥用 AES-256-GCM
加密，随机 12 字节 nonce，存储格式：

    版本号 1 字节（0x01） + nonce 12 字节 + 密文和标签

版本号是为以后轮换主密钥预留的，本期不做轮换。明文和密钥不进日志。
"""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

VERSION = 1
NONCE_SIZE = 12
KEY_SIZE = 32


class SecretDecryptError(Exception):
    """解密失败：版本未知、主密钥不对或密文被篡改。不携带明文和密钥信息。"""


def _master_key() -> bytes:
    return base64.b64decode(get_settings().MASTER_KEY, validate=True)


def encrypt_secret(plain: str) -> bytes:
    """加密一个密钥明文，返回可存入 api_key_cipher 的字节串。"""
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(_master_key()).encrypt(nonce, plain.encode("utf-8"), None)
    return bytes([VERSION]) + nonce + ciphertext


def decrypt_secret(blob: bytes) -> str:
    """解密 api_key_cipher。版本未知、标签校验失败时抛 SecretDecryptError。"""
    if len(blob) <= 1 + NONCE_SIZE or blob[0] != VERSION:
        raise SecretDecryptError("密钥密文无法解密")
    nonce = blob[1 : 1 + NONCE_SIZE]
    ciphertext = blob[1 + NONCE_SIZE :]
    try:
        plain = AESGCM(_master_key()).decrypt(nonce, ciphertext, None)
    except (InvalidTag, ValueError) as exc:
        raise SecretDecryptError("密钥密文无法解密") from exc
    return plain.decode("utf-8")


def last4(plain: str) -> str:
    """取末 4 位用于界面显示；不足 4 位时全部用 `•` 代替。"""
    if len(plain) < 4:
        return "•" * len(plain)
    return plain[-4:]
