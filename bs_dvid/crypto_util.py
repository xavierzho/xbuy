from __future__ import annotations

import hashlib
import secrets
import string

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from bs_dvid.constants import AES_IV


def md5_hash(data: str) -> str:
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def generate_random_key(length: int = 16) -> bytes:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length)).encode("utf-8")


def rc4_ksa(key: bytes) -> list[int]:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    return s


def rc4_prga_xor(s: list[int], data: bytes) -> bytes:
    s = s[:]
    i = j = 0
    out = bytearray(len(data))
    for idx, b in enumerate(data):
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        k = s[(s[i] + s[j]) % 256]
        out[idx] = b ^ k ^ 0x2A
    return bytes(out)


def rc4_encrypt(data: bytes, key: bytes) -> bytes:
    return rc4_prga_xor(rc4_ksa(key), data)


def rc4_decrypt(data: bytes, key: bytes) -> bytes:
    return rc4_encrypt(data, key)


def _normalize_aes_key(key: bytes) -> bytes:
    if len(key) < 16:
        return key + bytes(16 - len(key))
    if len(key) > 16:
        return key[:16]
    return key


def aes_encrypt_cbc(data: bytes, key: bytes) -> bytes:
    key_buffer = _normalize_aes_key(key if isinstance(key, bytes) else key.encode("utf-8"))
    cipher = AES.new(key_buffer, AES.MODE_CBC, AES_IV)
    encrypted = cipher.encrypt(pad(data, AES.block_size))
    md5_checksum = hashlib.md5(data).digest()
    return encrypted + md5_checksum


def aes_decrypt_cbc(data: bytes, key: bytes) -> bytes:
    key_buffer = _normalize_aes_key(key if isinstance(key, bytes) else key.encode("utf-8"))
    encrypted_data = data[:-16] if len(data) > 16 else data
    cipher = AES.new(key_buffer, AES.MODE_CBC, AES_IV)
    try:
        return unpad(cipher.decrypt(encrypted_data), AES.block_size)
    except ValueError:
        return cipher.decrypt(encrypted_data)
