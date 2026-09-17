"""TOTP (RFC 6238) + at-rest secret encryption. No new deps.

Verification is pure hmac/struct. The shared TOTP secret is stored encrypted
with Fernet, keyed off the existing AUTH_REFRESH_HASH_SECRET (already required,
already >= 32 bytes) — so no new key material to manage.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet

from .random import const_eq

STEP = 30          # seconds per TOTP window
DIGITS = 6


def random_secret() -> str:
    """A fresh base32 TOTP secret (160 bits), no padding — what QR/manual entry uses."""
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _b32decode(secret: str) -> bytes:
    pad = "=" * ((-len(secret)) % 8)
    return base64.b32decode(secret.upper() + pad, casefold=True)


def _hotp(key: bytes, counter: int) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def verify(secret: str, code: str, *, window: int = 1, at: float | None = None) -> bool:
    """True if `code` matches the TOTP for `secret` within ±window steps."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return False
    try:
        key = _b32decode(secret)
    except Exception:
        return False
    now = int((at if at is not None else time.time()) // STEP)
    for w in range(-window, window + 1):
        if const_eq(_hotp(key, now + w), code):
            return True
    return False


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    """otpauth:// URI for the authenticator QR / manual add."""
    label = quote(f"{issuer}:{account}")
    params = urlencode({
        "secret": secret, "issuer": issuer,
        "algorithm": "SHA1", "digits": DIGITS, "period": STEP,
    })
    return f"otpauth://totp/{label}?{params}"


# ---------- at-rest encryption of the secret ------------------------

def _fernet(refresh_hash_secret: bytes) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(refresh_hash_secret[:32]))


def encrypt_secret(secret: str, refresh_hash_secret: bytes) -> bytes:
    return _fernet(refresh_hash_secret).encrypt(secret.encode("ascii"))


def decrypt_secret(blob: bytes, refresh_hash_secret: bytes) -> str:
    return _fernet(refresh_hash_secret).decrypt(bytes(blob)).decode("ascii")
