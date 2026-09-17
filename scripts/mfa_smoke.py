"""MFA smoke — end-to-end 2FA (docs/PHASE15_AUTH.md M3).

Part A (offline): TOTP verify vector + secret encrypt/decrypt roundtrip.
Part B (DB + TestClient): create a throwaway local user, force global MFA,
then drive enrol -> verify(TOTP) -> backup-code -> wrong-code lockout ->
admin reset -> exemption-skips-MFA. Cleans up the user + restores the toggle.

Run:  uv run --env-file .env python scripts/mfa_smoke.py \
        --admin-email admin@aw.local --admin-password 'Correct-Horse-Battery-9!'
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from auth import totp

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}")
    if cond:
        _passed += 1
    else:
        _failed += 1


def _totp_now(secret: str, offset: int = 0) -> str:
    key = totp._b32decode(secret)
    return totp._hotp(key, int(time.time() // totp.STEP) + offset)


# ---------- Part A ----------

def part_a() -> None:
    print("Part A — TOTP core")
    from auth.deps import settings
    s = settings()
    secret = totp.random_secret()
    code = _totp_now(secret)
    check("verify accepts a current code", totp.verify(secret, code))
    check("verify rejects a wrong code", not totp.verify(secret, "000000"))
    blob = totp.encrypt_secret(secret, s.refresh_hash_secret)
    check("encrypt/decrypt roundtrip", totp.decrypt_secret(blob, s.refresh_hash_secret) == secret)


# ---------- Part B setup: throwaway user via the engine ----------

async def _create_user(email: str, password: str) -> str:
    from sqlalchemy import text
    from auth.deps import passwords
    from auth.engine import dispose, transaction
    from auth.random import new_id
    uid = new_id()
    ph = passwords().hash(password)
    async with transaction() as conn:
        await conn.execute(text("""
            INSERT INTO users (id, email, email_normalized, role, status,
                               password_hash, ver, created_at, updated_at)
            VALUES (:id, :e, :n, 'viewer', 'ACTIVE', :ph, 1, now(), now())
        """), {"id": uid, "e": email, "n": email.lower(), "ph": ph})
    await dispose()
    return uid


async def _cleanup(email: str) -> None:
    from sqlalchemy import text
    from auth.engine import dispose, transaction
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM users WHERE email_normalized = :n"), {"n": email.lower()})
    await dispose()


async def _set_mfa_required(value: bool) -> None:
    from sqlalchemy import text
    from auth.engine import dispose, transaction
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE app_settings SET value = CAST(:v AS jsonb) WHERE key = 'mfa_required'"),
            {"v": "true" if value else "false"},
        )
    await dispose()


def _clear_rl() -> None:
    """Wipe login rate-limit buckets — the smoke logs in many times from one IP,
    which would otherwise trip the 5/15min limit."""
    from db import connect
    conn = connect()
    conn.execute("DELETE FROM rate_limit_buckets")
    conn.commit()
    conn.close()


def _login(c, email: str, password: str):
    _clear_rl()
    return c.post("/api/v1/auth/login", json={"email": email, "password": password})


def part_b(admin_email: str, admin_password: str) -> None:
    print("Part B — full MFA flow")
    tag = f"mfasmoke-{int(time.time())}"
    email = f"{tag}@aw.local"
    pw = "Sup3r-Secret-Pw!"

    asyncio.run(_set_mfa_required(False))  # clean slate (survive a prior crash)
    asyncio.run(_create_user(email, pw))

    try:
        with TestClient(app) as c:
            # admin: header auth so it never collides with the user's cookie jar
            r = _login(c, admin_email, admin_password)
            ah = {"Authorization": f"Bearer {r.json()['access_token']}"}
            c.patch("/api/v1/settings", headers=ah, json={"mfa_required": True})
            c.cookies.clear()  # drop admin's cfv_refresh before the user flow

            # 1 · login -> enrol challenge (not a session)
            j = _login(c, email, pw).json()
            check("login returns mfa_required=enroll", j.get("mfa_required") and j.get("mfa_purpose") == "enroll")
            check("no access token issued yet", j.get("access_token") is None)
            check("cfv_mfa cookie set", "cfv_mfa" in c.cookies)

            # 2 · challenge info
            check("challenge info = enroll", c.get("/api/v1/auth/mfa/challenge").json().get("purpose") == "enroll")

            # 3 · enrol start + verify
            st = c.post("/api/v1/auth/mfa/enroll/start").json()
            secret = st["secret"]
            check("enroll/start returns otpauth uri", st.get("otpauth_uri", "").startswith("otpauth://totp/"))
            j = c.post("/api/v1/auth/mfa/enroll/verify", json={"code": _totp_now(secret)}).json()
            check("enroll/verify issues session", bool(j.get("access_token")))
            backup = j.get("backup_codes") or []
            check("enroll/verify returns 10 backup codes", len(backup) == 10)
            check("cfv_mfa cookie cleared after enroll", "cfv_mfa" not in c.cookies)

            # 4 · re-login now needs verify (enrolled)
            c.cookies.clear()
            check("re-login = verify purpose", _login(c, email, pw).json().get("mfa_purpose") == "verify")

            # 5 · verify with a fresh TOTP
            r = c.post("/api/v1/auth/mfa/verify", json={"code": _totp_now(secret), "method": "totp"})
            check("verify TOTP issues session", bool(r.json().get("access_token")))

            # 6 · verify with a backup code
            c.cookies.clear()
            _login(c, email, pw)
            r = c.post("/api/v1/auth/mfa/verify", json={"code": backup[0], "method": "backup"})
            check("verify backup-code issues session", bool(r.json().get("access_token")))
            # reused backup code now fails
            c.cookies.clear()
            _login(c, email, pw)
            r = c.post("/api/v1/auth/mfa/verify", json={"code": backup[0], "method": "backup"})
            check("reused backup code rejected", r.status_code == 400)

            # 7 · wrong code lockout (challenge burned after MAX_ATTEMPTS)
            c.cookies.clear()
            _login(c, email, pw)
            codes = [c.post("/api/v1/auth/mfa/verify", json={"code": "000000", "method": "totp"}).status_code
                     for _ in range(6)]
            check("wrong codes 400 then 401 (burned)", codes[0] == 400 and codes[-1] == 401)

            # 8 · admin resets the user's MFA -> enrolled=false
            uid = _find_uid(c, ah, email)
            r = c.post(f"/api/v1/users/{uid}/mfa/reset", headers=ah)
            check("admin mfa reset -> not enrolled", r.json().get("mfa_enrolled") is False)

            # 9 · exemption skips MFA entirely (straight to a session)
            c.patch(f"/api/v1/users/{uid}", headers=ah, json={"mfa_exempt": True})
            c.cookies.clear()
            j = _login(c, email, pw).json()
            check("exempt user logs in without MFA", bool(j.get("access_token")) and not j.get("mfa_required"))
    finally:
        asyncio.run(_set_mfa_required(False))
        asyncio.run(_cleanup(email))


def _find_uid(c, ah, email: str) -> str:
    j = c.get(f"/api/v1/users?q={email}", headers=ah).json()
    return j["users"][0]["id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    part_a()
    if not args.offline:
        global app
        from api import app  # imported here so --offline needs no DB/env
        part_b(args.admin_email, args.admin_password)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
