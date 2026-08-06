"""TokenService smoke test.

Round-trip, wrong-audience, wrong-issuer, expired, tampered, refresh
hash determinism, jti revocation.

Run:  uv run --env-file .env python scripts/token_smoke.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt as _jwt
from sqlalchemy import text

from auth.engine import dispose, transaction
from auth.settings import load
from auth.token_state import is_jti_revoked, prune_revocations, revoke_jti
from auth.tokens import ALGORITHM, TokenError, TokenService


def assert_raises(fn, exc):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}, got no error")


async def main() -> None:
    s = load()
    ts = TokenService(s)

    # ---- issue + verify round trip ----
    tok, ttl = ts.issue_access(user_id="u1", session_id="sess1", role="admin", ver=1)
    claims = ts.verify_access(tok)
    assert claims.sub == "u1" and claims.sid == "sess1" and claims.role == "admin"
    assert claims.ver == 1 and claims.exp - claims.iat == ttl
    print(f"[ok] round-trip — ttl={ttl}s, jti={claims.jti[:8]}…")

    # ---- wrong audience ----
    bad_aud = _jwt.encode(
        {"iss": s.jwt_issuer, "aud": "someone-else", "sub": "u1", "sid": "sess1",
         "jti": "j", "iat": int(time.time()), "exp": int(time.time()) + 60,
         "ver": 1, "role": "admin"},
        s.jwt_private_key_pem, algorithm=ALGORITHM, headers={"kid": s.jwt_kid},
    )
    assert_raises(lambda: ts.verify_access(bad_aud), TokenError)
    print("[ok] wrong audience rejected")

    # ---- wrong issuer ----
    bad_iss = _jwt.encode(
        {"iss": "not-us", "aud": s.jwt_audience, "sub": "u1", "sid": "sess1",
         "jti": "j", "iat": int(time.time()), "exp": int(time.time()) + 60,
         "ver": 1, "role": "admin"},
        s.jwt_private_key_pem, algorithm=ALGORITHM, headers={"kid": s.jwt_kid},
    )
    assert_raises(lambda: ts.verify_access(bad_iss), TokenError)
    print("[ok] wrong issuer rejected")

    # ---- expired ----
    now = int(time.time())
    expired = _jwt.encode(
        {"iss": s.jwt_issuer, "aud": s.jwt_audience, "sub": "u1", "sid": "sess1",
         "jti": "j", "iat": now - 3600, "exp": now - 60, "ver": 1, "role": "admin"},
        s.jwt_private_key_pem, algorithm=ALGORITHM, headers={"kid": s.jwt_kid},
    )
    assert_raises(lambda: ts.verify_access(expired), TokenError)
    print("[ok] expired token rejected")

    # ---- tampered signature ----
    tampered = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    assert_raises(lambda: ts.verify_access(tampered), TokenError)
    print("[ok] tampered token rejected")

    # ---- missing required claim ----
    missing = _jwt.encode(
        {"iss": s.jwt_issuer, "aud": s.jwt_audience, "sub": "u1",
         "iat": now, "exp": now + 60},  # no sid/jti/ver/role
        s.jwt_private_key_pem, algorithm=ALGORITHM, headers={"kid": s.jwt_kid},
    )
    assert_raises(lambda: ts.verify_access(missing), TokenError)
    print("[ok] missing required claim rejected")

    # ---- refresh token: uniqueness + deterministic hash + secret-keyed ----
    r1 = ts.new_refresh_token()
    r2 = ts.new_refresh_token()
    assert r1 != r2 and len(r1) >= 40
    h1a = ts.hash_refresh(r1); h1b = ts.hash_refresh(r1)
    h2 = ts.hash_refresh(r2)
    assert h1a == h1b and h1a != h2 and len(h1a) == 64  # hex sha256
    print(f"[ok] refresh tokens — unique, hash deterministic, {len(h1a)}-hex")

    # ---- jti revocation round-trip against real DB ----
    async with transaction() as conn:
        # Fresh jti — not revoked
        assert not await is_jti_revoked(conn, claims.jti)
        await revoke_jti(conn, claims.jti, claims.exp)
        assert await is_jti_revoked(conn, claims.jti)
        print("[ok] jti revocation persisted")

        # Prune only removes expired entries; ours is still valid → 0 removed
        removed = await prune_revocations(conn)
        assert removed == 0
        # Force-expire and re-prune
        await conn.execute(
            text("UPDATE access_token_revocations SET expires_at = now() - interval '1 hour' WHERE jti = :j"),
            {"j": claims.jti},
        )
        removed = await prune_revocations(conn)
        assert removed == 1
        print("[ok] revocation prune")

    # ---- public key export ----
    info = ts.public_key_info()
    assert info["kid"] == s.jwt_kid
    assert info["alg"] == ALGORITHM
    assert "BEGIN PUBLIC KEY" in info["pem"]
    print("[ok] public key export")

    await dispose()
    print("[done]")


if __name__ == "__main__":
    asyncio.run(main())
