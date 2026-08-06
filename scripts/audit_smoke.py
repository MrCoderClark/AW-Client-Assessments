"""End-to-end audit smoke: exercise several auth flows, verify the chain,
then check a few known-action rows landed.

Uses sync psycopg (via db.py) for DB peeks so we don't fight with
TestClient's event loop. The async chain verifier runs after TestClient
closes, via asyncio.run in a fresh loop.

Run:  uv run --env-file .env python scripts/audit_smoke.py \
        --admin-email admin@aw.local --admin-password 'Correct-Horse-Battery-9!'
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from api import app
from db import connect as sync_connect


def _last_id_sync() -> str | None:
    conn = sync_connect()
    r = conn.execute("SELECT id FROM audit_events ORDER BY at DESC, id DESC LIMIT 1").fetchone()
    conn.close()
    return str(r["id"]) if r else None


def _counts_since_sync(baseline_id: str | None) -> dict[str, int]:
    conn = sync_connect()
    if baseline_id is None:
        rows = conn.execute(
            "SELECT action, COUNT(*) AS n FROM audit_events GROUP BY action ORDER BY action"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT action, COUNT(*) AS n FROM audit_events "
            "WHERE at > (SELECT at FROM audit_events WHERE id = %s) "
            "GROUP BY action ORDER BY action",
            (baseline_id,),
        ).fetchall()
    conn.close()
    return {r["action"]: int(r["n"]) for r in rows}


async def _verify_async() -> dict:
    from auth.audit import verify_chain
    from auth.engine import dispose, transaction
    async with transaction() as conn:
        v = await verify_chain(conn)
    await dispose()
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    args = ap.parse_args()

    baseline_id = _last_id_sync()
    print(f"[start] baseline row id: {baseline_id or '(empty table)'}")

    with TestClient(app) as c:
        c.post("/api/v1/auth/login",
               json={"email": args.admin_email, "password": "wrong"})

        r = c.post("/api/v1/auth/login",
                   json={"email": args.admin_email, "password": args.admin_password})
        assert r.status_code == 200, r.text
        refresh_tok = r.json()["refresh_token"]

        # Rotate — old bearer becomes dead once the parent session is revoked.
        r = c.post("/api/v1/auth/refresh", json={"refresh_token": refresh_tok})
        assert r.status_code == 200
        b = f"Bearer {r.json()['access_token']}"

        c.post("/api/v1/auth/password/forgot", json={"email": args.admin_email})
        c.post("/api/v1/auth/password/forgot", json={"email": "nobody@nowhere.example"})

        r = c.post("/api/v1/auth/logout", headers={"Authorization": b})
        assert r.status_code == 204, r.text

    counts = _counts_since_sync(baseline_id)
    print(f"[ok] new rows this run:")
    for a, n in sorted(counts.items()):
        print(f"       {n:>2}  {a}")

    for expected in ("AUTH_LOGIN_FAILURE", "AUTH_LOGIN_SUCCESS", "AUTH_TOKEN_REFRESH",
                     "AUTH_PASSWORD_RESET_REQUESTED", "AUTH_LOGOUT"):
        assert counts.get(expected, 0) >= 1, f"missing expected action: {expected}"
    print("[ok] all expected actions present")

    verdict = asyncio.run(_verify_async())
    assert verdict["ok"], f"chain broken: {verdict}"
    print(f"[ok] chain verified — {verdict['checked']} row(s) total")
    print("[done]")


if __name__ == "__main__":
    main()
