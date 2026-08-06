"""End-to-end invite / accept / password-reset smoke.

Uses admin credentials to invite a fresh test user, extracts the token
from the returned invite_url, accepts the invite, verifies /me works,
then exercises forgot-password / reset-password.

Run:  uv run --env-file .env python scripts/invite_flow_smoke.py \
        --admin-email admin@aw.local --admin-password 'Correct-Horse-Battery-9!'
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import app
from fastapi.testclient import TestClient


def _extract_token(url: str) -> str:
    return parse_qs(urlparse(url).query).get("token", [""])[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    args = ap.parse_args()

    # Unique email per run so re-runs don't hit the "already exists" guard.
    invitee = f"test-{int(time.time())}@aw.local"

    with TestClient(app) as c:
        # ---- admin login ----
        r = c.post("/api/v1/auth/login",
                   json={"email": args.admin_email, "password": args.admin_password})
        assert r.status_code == 200, r.text
        admin_bearer = f"Bearer {r.json()['access_token']}"
        print(f"[ok] admin logged in as {args.admin_email}")

        # ---- invite ----
        r = c.post("/api/v1/auth/users",
                   headers={"Authorization": admin_bearer},
                   json={"email": invitee, "role": "operator", "first_name": "Auto", "last_name": "Test"})
        assert r.status_code == 201, r.text
        invite = r.json()
        token = _extract_token(invite["invite_url"])
        assert token, f"no token in url: {invite['invite_url']}"
        print(f"[ok] invited {invitee} -> {invite['user_id']}")
        print(f"     mail: {'sent' if invite['mail_ok'] else 'skipped'} — {invite['mail_note']}")

        # ---- weak password rejected ----
        r = c.post("/api/v1/auth/accept-invite",
                   json={"token": token, "password": "short", "first_name": "Auto", "last_name": "Test"})
        assert r.status_code == 400 and r.json()["code"] == "AUTH_PASSWORD_INVALID", r.text
        print("[ok] weak password on accept-invite -> 400 AUTH_PASSWORD_INVALID")

        # ---- accept invite + auto-login ----
        strong = "First-Correct-Battery-9!"
        r = c.post("/api/v1/auth/accept-invite",
                   json={"token": token, "password": strong, "first_name": "Auto", "last_name": "Test"})
        assert r.status_code == 200, r.text
        invitee_bearer = f"Bearer {r.json()['access_token']}"
        print("[ok] invite accepted -> tokens issued")

        # /me confirms user is ACTIVE and operator
        r = c.get("/api/v1/auth/me", headers={"Authorization": invitee_bearer})
        assert r.status_code == 200, r.text
        me = r.json()
        assert me["role"] == "operator" and me["status"] == "ACTIVE"
        assert "pdf:write" in me["permissions"] and "user:delete" not in me["permissions"]
        print(f"[ok] /me -> role={me['role']} status={me['status']} perms={len(me['permissions'])}")

        # ---- reusing the invite token is rejected ----
        r = c.post("/api/v1/auth/accept-invite",
                   json={"token": token, "password": strong, "first_name": "x", "last_name": "y"})
        assert r.status_code == 400 and r.json()["code"] == "AUTH_INVALID_TOKEN", r.text
        print("[ok] reused invite token -> 400 AUTH_INVALID_TOKEN")

        # ---- login flow works for the new user too ----
        r = c.post("/api/v1/auth/login", json={"email": invitee, "password": strong})
        assert r.status_code == 200, r.text
        print("[ok] invitee can log in normally")

        # ---- forgot-password always 202 ----
        r = c.post("/api/v1/auth/password/forgot", json={"email": invitee})
        assert r.status_code == 202, r.text
        r = c.post("/api/v1/auth/password/forgot", json={"email": "nobody@nowhere.example"})
        assert r.status_code == 202, r.text
        print("[ok] password/forgot 202 for both real and unknown email")

        # ---- reset-password with bad token -> 400 (the happy path can't be
        # tested here without intercepting the outbound email; SMTP is off in
        # smoke and the plaintext token never leaves the response) ----
        r = c.post("/api/v1/auth/password/reset",
                   json={"token": "bogus-token-does-not-exist", "new_password": "First-Correct-Battery-9!"})
        assert r.status_code == 400 and r.json()["code"] == "AUTH_INVALID_TOKEN", r.text
        print("[ok] password/reset with bad token -> 400 AUTH_INVALID_TOKEN")

        # ---- change-password works using the new user's Bearer ----
        r = c.post("/api/v1/auth/login", json={"email": invitee, "password": strong})
        assert r.status_code == 200
        b2 = f"Bearer {r.json()['access_token']}"
        r = c.post("/api/v1/auth/password/change",
                   headers={"Authorization": b2},
                   json={"current_password": strong, "new_password": "Second-Correct-Battery-9!"})
        assert r.status_code == 204, r.text
        # The old bearer should be dead (ver bumped).
        r = c.get("/api/v1/auth/me", headers={"Authorization": b2})
        assert r.status_code == 401, r.text
        print("[ok] change-password -> 204, old bearer 401 after ver bump")

        # ---- login with new password ----
        r = c.post("/api/v1/auth/login",
                   json={"email": invitee, "password": "Second-Correct-Battery-9!"})
        assert r.status_code == 200, r.text
        print("[ok] invitee can log in with the changed password")

    print("[done]")


if __name__ == "__main__":
    main()
