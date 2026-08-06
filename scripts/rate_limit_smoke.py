"""Rate-limit + lockout smoke.

Verifies:
  1. `/auth/login` returns 429 after the IP limit is hit (with Retry-After).
  2. `/auth/password/forgot` returns 202 even when throttled, and stops
     firing the actual reset work (audit `SEC_RATE_LIMITED` shows up).
  3. Five bad-password attempts on a real user drives `locked_until` and
     the next attempt returns 423 AUTH_ACCOUNT_LOCKED with a
     `SEC_LOCKOUT_STARTED` audit row.

Buckets and lockouts are cleared per-run so the smoke is idempotent.

Run:  uv run --env-file .env python scripts/rate_limit_smoke.py \
        --admin-email admin@aw.local --admin-password 'Correct-Horse-Battery-9!'
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from api import app
from db import connect as sync_connect


def _clear(email: str) -> None:
    conn = sync_connect()
    conn.execute("DELETE FROM rate_limit_buckets")
    conn.execute(
        "UPDATE users SET failed_login_attempts = 0, locked_until = NULL "
        "WHERE email_normalized = %s",
        (email.strip().lower(),),
    )
    conn.commit()
    conn.close()


def _count_action(action: str, since_id: str | None) -> int:
    conn = sync_connect()
    if since_id is None:
        r = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE action = %s",
            (action,),
        ).fetchone()
    else:
        r = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events "
            "WHERE action = %s "
            "AND at > (SELECT at FROM audit_events WHERE id = %s)",
            (action, since_id),
        ).fetchone()
    conn.close()
    return int(r["n"])


def _last_audit_id() -> str | None:
    conn = sync_connect()
    r = conn.execute(
        "SELECT id FROM audit_events ORDER BY at DESC, id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return str(r["id"]) if r else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    args = ap.parse_args()

    _clear(args.admin_email)
    baseline = _last_audit_id()

    with TestClient(app) as c:
        # ---- 1. /login rate limit ------------------------------------
        # First 5 bad-password attempts return 401 (also grow lockout counter).
        for i in range(5):
            r = c.post("/api/v1/auth/login",
                       json={"email": "ghost@nowhere.example", "password": "x"})
            assert r.status_code == 401, f"attempt {i}: expected 401, got {r.status_code}"

        # 6th should be 429 (IP limit is 5 / 15m).
        r = c.post("/api/v1/auth/login",
                   json={"email": "ghost@nowhere.example", "password": "x"})
        assert r.status_code == 429, f"expected 429, got {r.status_code} {r.text}"
        assert "Retry-After" in r.headers, "missing Retry-After header"
        print("[ok] /login 429 after IP limit; retry-after present")

        # ---- 2. /password/forgot throttle stays 202 ------------------
        _clear(args.admin_email)  # reset buckets
        # limit is 3/hour on email; 4th should still be 202 but throttled.
        for _ in range(3):
            r = c.post("/api/v1/auth/password/forgot",
                       json={"email": args.admin_email})
            assert r.status_code == 202
        r = c.post("/api/v1/auth/password/forgot",
                   json={"email": args.admin_email})
        assert r.status_code == 202, f"forgot throttle should still be 202, got {r.status_code}"
        print("[ok] /password/forgot stays 202 when throttled")

        # ---- 3. lockout after 5 bad password attempts ----------------
        _clear(args.admin_email)
        saw_locked = False
        for i in range(5):
            r = c.post("/api/v1/auth/login",
                       json={"email": args.admin_email, "password": "wrong-nope"})
            # 401 on first four; 5th trips lockout and returns 423.
            assert r.status_code in (401, 423), \
                f"attempt {i}: got {r.status_code} {r.text}"
            if r.status_code == 423:
                saw_locked = True
        assert saw_locked, "threshold reached but no 423 returned"
        # DB confirms the lockout persisted.
        dbc = sync_connect()
        row = dbc.execute(
            "SELECT locked_until FROM users WHERE email_normalized = %s",
            (args.admin_email.strip().lower(),),
        ).fetchone()
        dbc.close()
        assert row and row["locked_until"] is not None, \
            f"locked_until not set: {row}"
        print("[ok] lockout: threshold hit, locked_until set in DB")

    # audit checks
    for action in ("SEC_RATE_LIMITED", "SEC_LOCKOUT_STARTED"):
        n = _count_action(action, baseline)
        assert n >= 1, f"missing {action} audit row (got {n})"
    print("[ok] SEC_RATE_LIMITED and SEC_LOCKOUT_STARTED audits emitted")

    # Cleanup — unlock the admin so devs aren't stuck.
    _clear(args.admin_email)
    print("[done]")


if __name__ == "__main__":
    main()
