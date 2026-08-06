"""Notifications smoke.

Verifies:
  1. Role-gated fanout: scan_commit reaches admin + viewer; security only
     reaches admin/operator. Same INSERT...SELECT that the emit path uses.
  2. GET /api/v1/notifications is scoped to the calling user.
  3. Viewer GET filters out non-scan_commit categories via row ownership.
  4. mark-read / read-all zero the unread counter.
  5. HTTP-triggered emit path still works: inviting a user emits a
     user_lifecycle notification to admins.

Run:  uv run --env-file .env python scripts/notifications_smoke.py \
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


def _cleanup(marker: str) -> None:
    conn = sync_connect()
    conn.execute("DELETE FROM users WHERE email_normalized LIKE %s", (f"{marker}%",))
    conn.commit()
    conn.close()


def _fanout_direct(category: str, kind: str, severity: str, title: str, roles: list[str]) -> int:
    """Direct SQL fanout — mirrors auth.notifications.emit_for_category
    so the smoke doesn't have to reach across event loops.
    Returns number of rows inserted."""
    conn = sync_connect()
    role_array = "{" + ",".join(roles) + "}"
    r = conn.execute(
        """INSERT INTO notifications
              (id, user_id, category, kind, severity, title, body, url, context_json)
            SELECT gen_random_uuid(), u.id, %s, %s, %s, %s, NULL, NULL, '{}'::jsonb
            FROM users u
            WHERE u.status = 'ACTIVE' AND u.deleted_at IS NULL
              AND u.role = ANY(%s::user_role[])
            RETURNING id""",
        (category, kind, severity, title, role_array),
    )
    n = len(r.fetchall())
    conn.commit()
    conn.close()
    return n


def _counts(user_email: str) -> dict[str, int]:
    conn = sync_connect()
    r = conn.execute(
        """SELECT n.category, COUNT(*) AS c
             FROM notifications n JOIN users u ON u.id = n.user_id
             WHERE u.email_normalized = %s
             GROUP BY n.category""",
        (user_email.lower(),),
    ).fetchall()
    conn.close()
    return {row["category"]: int(row["c"]) for row in r}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    args = ap.parse_args()

    marker = f"smoke-notif-{int(time.time())}"
    viewer_email = f"{marker}-viewer@example.test"
    _cleanup(marker)

    with TestClient(app) as c:
        # ---- login admin ---------------------------------------------
        r = c.post("/api/v1/auth/login",
                   json={"email": args.admin_email, "password": args.admin_password})
        assert r.status_code == 200, r.text
        admin_bearer = f"Bearer {r.json()['access_token']}"

        # ---- invite viewer (also exercises the HTTP emit path) -------
        r = c.post("/api/v1/auth/users",
                   headers={"Authorization": admin_bearer},
                   json={"email": viewer_email, "role": "viewer",
                         "first_name": "V", "last_name": "iewer"})
        assert r.status_code == 201, r.text
        viewer_id = r.json()["user_id"]

        # Activate the viewer so they can log in.
        conn = sync_connect()
        from auth.passwords import PasswordService
        from auth.settings import load
        pw_hash = PasswordService(load()).hash("Test-Notif-Pass-99!")
        conn.execute(
            """UPDATE users
                 SET password_hash = %s, status = 'ACTIVE',
                     email_verified_at = COALESCE(email_verified_at, now())
                 WHERE id = %s""",
            (pw_hash, viewer_id),
        )
        conn.commit()
        conn.close()

        r = c.post("/api/v1/auth/login",
                   json={"email": viewer_email, "password": "Test-Notif-Pass-99!"})
        assert r.status_code == 200, r.text
        viewer_bearer = f"Bearer {r.json()['access_token']}"
        print("[ok] admin + viewer logged in")

        # ---- 1. HTTP-triggered user_lifecycle fanout -----------------
        # The invite above already emitted a user_lifecycle to admin.
        by_cat_a = _counts(args.admin_email)
        assert by_cat_a.get("user_lifecycle", 0) >= 1, f"admin missing user_lifecycle: {by_cat_a}"
        by_cat_v = _counts(viewer_email)
        assert by_cat_v.get("user_lifecycle", 0) == 0, f"viewer should NOT see user_lifecycle: {by_cat_v}"
        print("[ok] invite emitted user_lifecycle → admin only")

        # ---- 2. Direct fanout: scan_commit reaches everyone ---------
        n = _fanout_direct("scan_commit", "scan_completed", "INFO",
                           "Test scan complete", ["admin", "operator", "viewer"])
        assert n >= 2, f"expected at least admin+viewer, got {n}"
        assert _counts(args.admin_email).get("scan_commit", 0) >= 1
        assert _counts(viewer_email).get("scan_commit", 0) >= 1
        print(f"[ok] scan_commit fanout: {n} rows across active users")

        # ---- 3. Direct fanout: security bypasses viewer -------------
        _fanout_direct("security", "test_sec", "SEC", "Test security event",
                       ["admin", "operator"])
        assert _counts(args.admin_email).get("security", 0) >= 1
        assert _counts(viewer_email).get("security", 0) == 0
        print("[ok] security fanout: admin sees it, viewer does not")

        # ---- 4. GET /notifications: viewer only sees scan_commit ----
        r = c.get("/api/v1/notifications",
                  headers={"Authorization": viewer_bearer})
        assert r.status_code == 200, r.text
        jv = r.json()
        cats_v = {n["category"] for n in jv["notifications"]}
        assert cats_v == {"scan_commit"}, f"viewer categories should be {{scan_commit}}: {cats_v}"
        assert jv["unread"] == len(jv["notifications"])
        print(f"[ok] viewer GET: {jv['unread']} unread, only scan_commit")

        # ---- 5. Admin sees at least 3 categories --------------------
        r = c.get("/api/v1/notifications",
                  headers={"Authorization": admin_bearer})
        assert r.status_code == 200, r.text
        ja = r.json()
        cats_a = {n["category"] for n in ja["notifications"]}
        assert "scan_commit" in cats_a and "security" in cats_a and "user_lifecycle" in cats_a, cats_a
        print(f"[ok] admin GET: {ja['unread']} unread, categories={sorted(cats_a)}")

        # ---- 6. Mark-one-read ---------------------------------------
        first_id = ja["notifications"][0]["id"]
        r = c.post(f"/api/v1/notifications/{first_id}/read",
                   headers={"Authorization": admin_bearer})
        assert r.status_code == 204, r.text
        # second call: 404
        r = c.post(f"/api/v1/notifications/{first_id}/read",
                   headers={"Authorization": admin_bearer})
        assert r.status_code == 404
        print("[ok] mark-one-read + second call 404")

        # ---- 7. Mark-all-read zeroes it -----------------------------
        r = c.post("/api/v1/notifications/read-all",
                   headers={"Authorization": admin_bearer})
        assert r.status_code == 200
        left = c.get("/api/v1/notifications",
                     headers={"Authorization": admin_bearer}).json()
        assert left["unread"] == 0, left
        print("[ok] mark-all-read → unread == 0")

    _cleanup(marker)
    print("[done]")


if __name__ == "__main__":
    main()
