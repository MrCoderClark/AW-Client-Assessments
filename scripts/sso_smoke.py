"""Entra SSO smoke (docs/ENTRA_SSO.md §13, steps S2/S3).

Part A — OFFLINE, no DB/env needed: PKCE challenge, signed state cookie
  (roundtrip + tamper + expiry), authorize-URL params, and id_token
  verification (happy path + nonce/tenant/expiry failures) using a locally
  minted RS256 token so no live tenant is required.

Part B — DB-backed: resolve_sso_user JIT-create / idempotent / email-link /
  disabled-deny, against throwaway users that are cleaned up afterward.
  Needs the migration applied (alembic upgrade head) + a reachable DB.

Run:  uv run --env-file .env python scripts/sso_smoke.py
      uv run python scripts/sso_smoke.py --offline    # Part A only
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt as _jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from auth import sso

_TEST_SECRET = b"\x11" * 32
_TENANT = "11111111-1111-1111-1111-111111111111"
_CLIENT = "22222222-2222-2222-2222-222222222222"
_ISSUER = f"https://login.microsoftonline.com/{_TENANT}/v2.0"

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    mark = "OK  " if cond else "FAIL"
    print(f"  [{mark}] {name}")
    if cond:
        _passed += 1
    else:
        _failed += 1


def expect_raises(name: str, fn) -> None:
    try:
        fn()
        check(name, False)
    except sso.SsoError:
        check(name, True)


# ---------- Part A: offline crypto ---------------------------------

def _new_rsa() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub


def _mint_id_token(priv: bytes, *, nonce: str, tid: str = _TENANT, aud: str = _CLIENT,
                   exp_delta: int = 300, oid: str = "oid-abc", email: str = "user@aw.local") -> str:
    now = int(time.time())
    claims = {
        "iss": _ISSUER, "aud": aud, "sub": "sub-1", "oid": oid, "tid": tid,
        "nonce": nonce, "email": email, "name": "Test User",
        "given_name": "Test", "family_name": "User",
        "iat": now, "exp": now + exp_delta,
    }
    return _jwt.encode(claims, priv, algorithm="RS256", headers={"kid": "test-kid"})


def part_a() -> None:
    print("Part A — offline crypto")

    # PKCE
    verifier, challenge = sso.pkce_pair()
    import base64, hashlib
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    check("pkce challenge = base64url(sha256(verifier))", challenge == expected)
    check("pkce verifier length in [43,128]", 43 <= len(verifier) <= 128)

    # state cookie roundtrip
    payload = {"state": "s1", "nonce": "n1", "verifier": verifier,
               "exp": int(time.time()) + 300}
    cookie = sso.sign_state_cookie(payload, _TEST_SECRET)
    got = sso.read_state_cookie(cookie, _TEST_SECRET)
    check("state cookie roundtrip preserves state", got["state"] == "s1")

    # tamper: flip a char in the body
    body, sig = cookie.split(".", 1)
    tampered = ("A" if body[0] != "A" else "B") + body[1:] + "." + sig
    expect_raises("state cookie tamper rejected", lambda: sso.read_state_cookie(tampered, _TEST_SECRET))

    # wrong secret rejected
    expect_raises("state cookie wrong-secret rejected",
                  lambda: sso.read_state_cookie(cookie, b"\x22" * 32))

    # expired rejected
    old = sso.sign_state_cookie({"state": "s", "nonce": "n", "verifier": "v",
                                 "exp": int(time.time()) - 1}, _TEST_SECRET)
    expect_raises("state cookie expiry rejected", lambda: sso.read_state_cookie(old, _TEST_SECRET))

    # authorize URL
    url = sso.build_authorize_url(
        authorize_endpoint="https://login.microsoftonline.com/t/oauth2/v2.0/authorize",
        client_id=_CLIENT, redirect_uri="http://localhost:8000/api/v1/auth/sso/callback",
        scope="openid profile email", state="s1", nonce="n1", code_challenge=challenge,
    )
    check("authorize url has code_challenge_method=S256", "code_challenge_method=S256" in url)
    check("authorize url has response_type=code", "response_type=code" in url)
    check("authorize url carries the nonce", "nonce=n1" in url)

    # id_token verify — happy path
    priv, pub = _new_rsa()
    tok = _mint_id_token(priv, nonce="n1")
    claims = sso.verify_id_token(tok, signing_key=pub, issuer=_ISSUER, audience=_CLIENT,
                                 nonce="n1", tenant_id=_TENANT)
    check("id_token verify returns oid subject", claims.subject == "oid-abc")
    check("id_token verify extracts email", claims.email == "user@aw.local")

    # nonce mismatch
    expect_raises("id_token nonce mismatch rejected",
                  lambda: sso.verify_id_token(tok, signing_key=pub, issuer=_ISSUER,
                                              audience=_CLIENT, nonce="WRONG", tenant_id=_TENANT))
    # tenant mismatch (SEC-SSO-06)
    expect_raises("id_token wrong-tenant rejected",
                  lambda: sso.verify_id_token(tok, signing_key=pub, issuer=_ISSUER,
                                              audience=_CLIENT, nonce="n1", tenant_id="other-tid"))
    # expired
    exp_tok = _mint_id_token(priv, nonce="n1", exp_delta=-1000)
    expect_raises("id_token expired rejected",
                  lambda: sso.verify_id_token(exp_tok, signing_key=pub, issuer=_ISSUER,
                                              audience=_CLIENT, nonce="n1", tenant_id=_TENANT))
    # wrong key (signature fails)
    _, other_pub = _new_rsa()
    expect_raises("id_token wrong-key rejected",
                  lambda: sso.verify_id_token(tok, signing_key=other_pub, issuer=_ISSUER,
                                              audience=_CLIENT, nonce="n1", tenant_id=_TENANT))


# ---------- Part B: resolve_sso_user against the DB ----------------

async def part_b() -> None:
    print("Part B — resolve_sso_user (DB)")
    from auth.deps import auth_service
    from auth.engine import dispose, transaction
    from auth.random import new_id
    from auth.sso import SsoClaims
    from sqlalchemy import text

    svc = auth_service()
    tag = f"ssosmoke-{int(time.time())}"
    email_jit = f"{tag}-jit@aw.local"
    email_link = f"{tag}-link@aw.local"
    sub_jit = f"oid-{tag}-jit"
    sub_link = f"oid-{tag}-link"
    created_norms = [email_jit.lower(), email_link.lower()]

    def claims(email: str, sub: str) -> SsoClaims:
        return SsoClaims(subject=sub, tenant="tid-smoke", email=email,
                         display_name="Smoke User", first_name="Smoke", last_name="User")

    try:
        # 1 · JIT create
        async with transaction() as conn:
            uid1, role1, ver1 = await svc.resolve_sso_user(
                conn, claims=claims(email_jit, sub_jit),
                ip_address="127.0.0.1", user_agent="smoke", request_id="rid-1")
        check("JIT create returns viewer", role1 == "viewer" and ver1 == 1)

        async with transaction() as conn:
            row = (await conn.execute(text(
                "SELECT status, password_hash, email_verified_at, sso_subject "
                "FROM users WHERE id = :id"), {"id": uid1})).first()
        check("JIT user is ACTIVE", str(row.status) == "ACTIVE")
        check("JIT user has NULL password_hash", row.password_hash is None)
        check("JIT user email marked verified", row.email_verified_at is not None)
        check("JIT user linked to subject", str(row.sso_subject) == sub_jit)

        # 2 · idempotent — same subject, no new row
        async with transaction() as conn:
            uid2, _, _ = await svc.resolve_sso_user(
                conn, claims=claims(email_jit, sub_jit),
                ip_address="127.0.0.1", user_agent="smoke", request_id="rid-2")
        check("second resolve returns same user", uid2 == uid1)

        # 3 · link by email onto an existing password row (role preserved)
        async with transaction() as conn:
            uidL = new_id()
            await conn.execute(text("""
                INSERT INTO users (id, email, email_normalized, role, status, ver,
                                   password_hash, created_at, updated_at)
                VALUES (:id, :email, :norm, 'operator', 'ACTIVE', 1,
                        'x', now(), now())
            """), {"id": uidL, "email": email_link, "norm": email_link.lower()})
        async with transaction() as conn:
            uid3, role3, _ = await svc.resolve_sso_user(
                conn, claims=claims(email_link, sub_link),
                ip_address="127.0.0.1", user_agent="smoke", request_id="rid-3")
        check("email-link targets existing row", uid3 == uidL)
        check("email-link preserves role (operator)", role3 == "operator")
        async with transaction() as conn:
            row = (await conn.execute(text(
                "SELECT sso_subject FROM users WHERE id = :id"), {"id": uidL})).first()
        check("email-link stamped subject", str(row.sso_subject) == sub_link)

        # 4 · disabled deny
        async with transaction() as conn:
            await conn.execute(text(
                "UPDATE users SET status='SUSPENDED' WHERE id = :id"), {"id": uid1})
        from auth.service import AccountDisabled
        denied = False
        try:
            async with transaction() as conn:
                await svc.resolve_sso_user(
                    conn, claims=claims(email_jit, sub_jit),
                    ip_address="127.0.0.1", user_agent="smoke", request_id="rid-4")
        except AccountDisabled:
            denied = True
        check("suspended user denied", denied)

    finally:
        # cleanup throwaway users (audit rows are append-only, left in place)
        async with transaction() as conn:
            await conn.execute(
                text("DELETE FROM users WHERE email_normalized = ANY(:ns)"),
                {"ns": created_norms},
            )
        await dispose()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="Part A only (no DB)")
    args = ap.parse_args()

    part_a()
    if not args.offline:
        asyncio.run(part_b())

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
