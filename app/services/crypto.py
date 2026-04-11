"""AES-256-CBC encryption/decryption for VNC passwords."""

import os
import base64
import hashlib
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


def _get_key() -> bytes:
    """Derive a 32-byte AES key from the environment variable."""
    raw = os.environ.get("VNC_ENCRYPTION_KEY", "vnc-monitor-default-key-change-me")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_password(plaintext: str) -> str:
    """Encrypt a plaintext password with AES-256-CBC.

    Returns a base64-encoded string of IV + ciphertext.
    If plaintext is empty, returns empty string.
    """
    if not plaintext:
        return ""
    key = _get_key()
    iv = os.urandom(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ct = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    return base64.b64encode(iv + ct).decode("utf-8")


def decrypt_password(ciphertext: str) -> str:
    """Decrypt an AES-256-CBC encrypted password.

    Expects base64-encoded IV + ciphertext.  Returns empty string
    if ciphertext is empty or decryption fails.
    """
    if not ciphertext:
        return ""
    try:
        key = _get_key()
        raw = base64.b64decode(ciphertext)
        iv = raw[:16]
        ct = raw[16:]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(ct), AES.block_size).decode("utf-8")
    except Exception:
        return ""
