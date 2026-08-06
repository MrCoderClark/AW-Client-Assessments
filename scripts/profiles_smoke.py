"""Profiles + dashboard-layout smoke.

End-to-end sweep of /api/v1/profiles/* and the profile field on /me and
/api/v1/users/{id}:
  - three system profiles are seeded (Operations, Fleet Health, Records)
  - admin can create a custom profile
  - name uniqueness enforced (case-insensitive)
  - unknown layout_key rejected
  - system profile cannot be renamed or deleted
  - profile can be assigned to a user via PATCH /users
  - deletion refused while users still assigned
  - reassignment then delete succeeds
  - /me carries the profile summary

Run:  uv run --env-file .env python scripts/profiles_smoke.py \
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


def _cleanup(email_marker: str, profile_marker: str) -> None:
    conn = sync_connect()
    conn.execute(
        "DELETE FROM users WHERE email_normalized LIKE %s",
        (f"{email_marker}%",),
    )
    conn.execute(
        "DELETE FROM profiles WHERE name LIKE %s AND is_system = false",
        (f"{profile_marker}%",),
    )
    conn.commit()
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    args = ap.parse_args()

    email_marker = f"smoke-prof-{int(time.time())}"
    profile_marker = f"SmokeProf-{int(time.time())}"
    invitee_email = f"{email_marker}@example.test"

    _cleanup(email_marker, profile_marker)

    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login",
                   json={"email": args.admin_email, "password": args.admin_password})
        assert r.status_code == 200, r.text
        access = r.json()["access_token"]
        h = {"Authorization": f"Bearer {access}"}
        print("[ok] admin logged in")

        # ---- /me carries the profile field (null for admin by default) ----
        me = c.get("/api/v1/auth/me", headers=h).json()
        assert "profile" in me, me
        print(f"[ok] /me has profile field (value: {me['profile']})")

        # ---- list — 3 seeded system profiles are present -----------------
        r = c.get("/api/v1/profiles", headers=h)
        assert r.status_code == 200, r.text
        rows = r.json()["profiles"]
        sys_rows = [p for p in rows if p["is_system"]]
        assert len(sys_rows) >= 3, sys_rows
        layouts = {p["layout_key"] for p in sys_rows}
        assert {"ops_default", "fleet_health", "records"} <= layouts, layouts
        print(f"[ok] {len(sys_rows)} system profiles seeded ({', '.join(sorted(layouts))})")

        # ---- create custom profile ---------------------------------------
        r = c.post("/api/v1/profiles", headers=h, json={
            "name": profile_marker,
            "description": "smoke",
            "layout_key": "fleet_health",
        })
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert r.json()["is_system"] is False
        print(f"[ok] created custom profile {pid}")

        # ---- dup name (case-insensitive) rejected ------------------------
        r = c.post("/api/v1/profiles", headers=h, json={
            "name": profile_marker.upper(),
            "description": None, "layout_key": "records",
        })
        assert r.status_code == 409, r.text
        print("[ok] duplicate name rejected 409")

        # ---- unknown layout_key rejected ---------------------------------
        r = c.post("/api/v1/profiles", headers=h, json={
            "name": f"{profile_marker}-bad", "description": None,
            "layout_key": "nonesuch",
        })
        assert r.status_code == 400, r.text
        print("[ok] unknown layout_key rejected 400")

        # ---- system profile rename refused, but description/layout allowed
        sys_ops = next(p for p in sys_rows if p["layout_key"] == "ops_default")
        r = c.patch(f"/api/v1/profiles/{sys_ops['id']}", headers=h,
                    json={"name": "Renamed Ops"})
        assert r.status_code == 409, r.text
        r = c.patch(f"/api/v1/profiles/{sys_ops['id']}", headers=h,
                    json={"description": sys_ops["description"] or ""})
        assert r.status_code == 200, r.text
        print("[ok] system profile: name locked, description editable")

        # ---- system profile delete refused -------------------------------
        r = c.delete(f"/api/v1/profiles/{sys_ops['id']}", headers=h)
        assert r.status_code == 409, r.text
        print("[ok] system profile delete refused 409")

        # ---- invite a throwaway user, assign profile, check /users row ---
        r = c.post("/api/v1/auth/users", headers=h, json={
            "email": invitee_email, "role": "viewer",
            "first_name": "P", "last_name": "S",
        })
        assert r.status_code == 201, r.text
        uid = r.json()["user_id"]

        r = c.patch(f"/api/v1/users/{uid}", headers=h, json={"profile_id": pid})
        assert r.status_code == 200, r.text
        assert r.json()["profile"] and r.json()["profile"]["id"] == pid
        assert r.json()["profile"]["layout_key"] == "fleet_health"
        print(f"[ok] assigned custom profile to {invitee_email}")

        # ---- delete refused while user assigned --------------------------
        r = c.delete(f"/api/v1/profiles/{pid}", headers=h)
        assert r.status_code == 409, r.text
        assert "user" in r.json().get("detail", "").lower()
        print("[ok] delete refused while user assigned 409")

        # ---- clear assignment (empty string) -----------------------------
        r = c.patch(f"/api/v1/users/{uid}", headers=h, json={"profile_id": ""})
        assert r.status_code == 200, r.text
        assert r.json()["profile"] is None
        print("[ok] cleared profile assignment with empty string")

        # ---- delete succeeds now -----------------------------------------
        r = c.delete(f"/api/v1/profiles/{pid}", headers=h)
        assert r.status_code == 204, r.text
        r = c.get("/api/v1/profiles", headers=h)
        assert not any(p["id"] == pid for p in r.json()["profiles"])
        print("[ok] deleted custom profile after reassignment")

    _cleanup(email_marker, profile_marker)
    print("[ok] all profile smokes passed")


if __name__ == "__main__":
    main()
