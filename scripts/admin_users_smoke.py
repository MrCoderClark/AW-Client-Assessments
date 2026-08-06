"""Admin user-endpoints smoke.

End-to-end sweep of /api/v1/users/* — invite a fresh throwaway user,
then exercise list, get, patch, suspend, reactivate, force-reset, and
soft-delete. Verifies audit rows for each mutation and that
self-protection blocks admin from suspending/deleting themselves.

Run:  uv run --env-file .env python scripts/admin_users_smoke.py \
        --admin-email admin@aw.local --admin-password 'Correct-Horse-Battery-9!'
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from api import app
from db import connect as sync_connect


def _cleanup(email_prefix: str) -> None:
    conn = sync_connect()
    conn.execute(
        "DELETE FROM users WHERE email_normalized LIKE %s",
        (f"{email_prefix}%",),
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
            "WHERE action = %s AND at > (SELECT at FROM audit_events WHERE id = %s)",
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

    marker = f"smoke-admin-{int(time.time())}"
    invitee_email = f"{marker}@example.test"

    _cleanup(marker)
    baseline = _last_audit_id()

    with TestClient(app) as c:
        # ---- login admin ---------------------------------------------
        r = c.post("/api/v1/auth/login",
                   json={"email": args.admin_email, "password": args.admin_password})
        assert r.status_code == 200, r.text
        access = r.json()["access_token"]
        h = {"Authorization": f"Bearer {access}"}
        print("[ok] admin logged in")

        # ---- invite fresh user (existing endpoint) -------------------
        r = c.post("/api/v1/auth/users", headers=h, json={
            "email": invitee_email, "role": "viewer",
            "first_name": "Test", "last_name": "User",
        })
        assert r.status_code == 201, r.text
        uid = r.json()["user_id"]
        print(f"[ok] invited {invitee_email}")

        # ---- list users, ours should be in there ---------------------
        r = c.get("/api/v1/users", headers=h,
                  params={"q": marker, "limit": 5})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["total"] >= 1
        assert any(u["id"] == uid for u in data["users"]), \
            f"invited user not in list: {data}"
        print(f"[ok] list found user ({data['total']} match)")

        # ---- get single ---------------------------------------------
        r = c.get(f"/api/v1/users/{uid}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "INVITED"
        print("[ok] get single user")

        # ---- patch profile ------------------------------------------
        r = c.patch(f"/api/v1/users/{uid}", headers=h, json={
            "first_name": "Renamed",
            "display_name": "Renamed User",
        })
        assert r.status_code == 200, r.text
        assert r.json()["first_name"] == "Renamed"
        assert r.json()["display_name"] == "Renamed User"
        print("[ok] patch profile")

        # ---- patch role change bumps ver + revokes sessions ---------
        r = c.patch(f"/api/v1/users/{uid}", headers=h, json={"role": "operator"})
        assert r.status_code == 200, r.text
        ver_after = r.json()["ver"]
        assert r.json()["role"] == "operator"
        print(f"[ok] role change (ver -> {ver_after})")

        # ---- suspend ------------------------------------------------
        r = c.post(f"/api/v1/users/{uid}/suspend", headers=h,
                   json={"reason": "smoke test"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUSPENDED"
        assert r.json()["suspended_reason"] == "smoke test"
        print("[ok] suspend")

        # ---- reactivate ---------------------------------------------
        r = c.post(f"/api/v1/users/{uid}/reactivate", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACTIVE"
        assert r.json()["suspended_reason"] is None
        print("[ok] reactivate")

        # ---- force-reset --------------------------------------------
        r = c.post(f"/api/v1/users/{uid}/force-reset", headers=h)
        assert r.status_code == 200, r.text
        assert "reset_url" in r.json()
        # confirm the user's must_change_password flipped on
        r2 = c.get(f"/api/v1/users/{uid}", headers=h)
        assert r2.json()["must_change_password"] is True
        print("[ok] force-reset (must_change_password flag set)")

        # ---- self-protection: admin cannot suspend or delete self ---
        admin_row = c.get("/api/v1/auth/me", headers=h).json()
        admin_uid = admin_row["user_id"]
        r = c.post(f"/api/v1/users/{admin_uid}/suspend", headers=h, json={"reason": "x"})
        assert r.status_code == 409, f"expected self-suspend blocked, got {r.status_code}"
        r = c.delete(f"/api/v1/users/{admin_uid}", headers=h)
        assert r.status_code == 409, f"expected self-delete blocked, got {r.status_code}"
        print("[ok] self-protection blocks suspend/delete of admin")

        # ---- soft delete --------------------------------------------
        r = c.delete(f"/api/v1/users/{uid}", headers=h)
        assert r.status_code == 204, r.text
        # should now be filtered out by default
        r = c.get("/api/v1/users", headers=h, params={"q": marker})
        assert not any(u["id"] == uid for u in r.json()["users"]), \
            "soft-deleted user still visible in default list"
        # but reappears with include_deleted=true
        r = c.get("/api/v1/users", headers=h,
                  params={"q": marker, "include_deleted": "true"})
        soft = [u for u in r.json()["users"] if u["id"] == uid]
        assert soft and soft[0]["status"] == "SOFT_DELETED"
        print("[ok] soft delete (hidden by default, visible when included)")

        # ---- hard delete --------------------------------------------
        r = c.delete(f"/api/v1/users/{uid}", headers=h, params={"hard": "true"})
        assert r.status_code == 204, r.text
        r = c.get(f"/api/v1/users/{uid}", headers=h)
        assert r.status_code == 404, "hard-deleted user still fetchable"
        print("[ok] hard delete")

    # audit checks
    for action in ("USER_UPDATED", "USER_SUSPENDED", "USER_REACTIVATED",
                   "USER_FORCE_RESET", "USER_SOFT_DELETED", "USER_HARD_DELETED"):
        n = _count_action(action, baseline)
        assert n >= 1, f"missing {action} audit row (got {n})"
    print("[ok] all expected audit rows present")
    print("[done]")


if __name__ == "__main__":
    main()
