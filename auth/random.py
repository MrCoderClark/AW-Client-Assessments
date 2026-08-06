"""Random + comparison primitives. All auth secrets flow through here.

ponytail: uuid4 for row ids is fine at M1 scale — swap to a uuidv7 impl
if audit-log time-sortability becomes measurable pain.
"""
from __future__ import annotations

import hmac
import secrets
import uuid


def new_id() -> str:
    """Row id (uuid4 string). See ponytail note above."""
    return str(uuid.uuid4())


def opaque_token(nbytes: int = 32) -> str:
    """URL-safe token (base64url of `nbytes` random bytes). Default 32 = 256 bits."""
    return secrets.token_urlsafe(nbytes)


def numeric_code(digits: int = 6) -> str:
    """Zero-padded numeric OTP. Default 6 digits for email/SMS."""
    if not (4 <= digits <= 10):
        raise ValueError("digits out of range")
    n = secrets.randbelow(10**digits)
    return str(n).zfill(digits)


def const_eq(a: str, b: str) -> bool:
    """Constant-time string compare. Use for tokens, never for hashes."""
    return hmac.compare_digest(a.encode(), b.encode())
