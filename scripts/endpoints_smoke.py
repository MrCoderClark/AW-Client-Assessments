"""End-to-end HTTP smoke over TestClient.

Full lifecycle: bad login → good login → me → refresh → me (new token) →
refresh reuse detection → logout → me (401).

Run:  uv run --env-file .env python scripts/endpoints_smoke.py \
        --email admin@aw.local --password 'Correct-Horse-Battery-9!'
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from api import app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    with TestClient(app) as client:
        # ---- bad login → 401 problem+json ----
        r = client.post("/api/v1/auth/login",
                        json={"email": args.email, "password": "WRONG"})
        assert r.status_code == 401, r.text
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["code"] == "AUTH_INVALID_CREDENTIALS"
        print("[ok] bad login → 401 problem+json")

        # ---- good login → 200 with tokens ----
        r = client.post("/api/v1/auth/login",
                        json={"email": args.email, "password": args.password})
        assert r.status_code == 200, r.text
        tokens = r.json()
        assert tokens["access_token"] and tokens["refresh_token"]
        assert tokens["expires_in"] == 900
        access = tokens["access_token"]
        refresh = tokens["refresh_token"]
        print(f"[ok] login → tokens (access exp {tokens['expires_in']}s)")

        # ---- /me with the access token ----
        r = client.get("/api/v1/auth/me",
                       headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200, r.text
        me = r.json()
        assert me["email"] == args.email
        assert me["role"] == "admin"
        assert "user:delete" in me["permissions"]
        print(f"[ok] /me → {me['email']} role={me['role']} perms={len(me['permissions'])}")

        # ---- /me without token → 401 ----
        r = client.get("/api/v1/auth/me")
        assert r.status_code == 401, r.text
        print("[ok] /me anonymous → 401")

        # ---- refresh (using body, since TestClient carries the cookie too) ----
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r.status_code == 200, r.text
        rotated = r.json()
        assert rotated["access_token"] != access
        assert rotated["refresh_token"] != refresh
        print("[ok] refresh → rotated access + refresh tokens")

        # ---- reuse old refresh → 401 + all sessions revoked ----
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r.status_code == 401, r.text
        assert r.json()["code"] == "AUTH_REFRESH_INVALID"
        print("[ok] refresh reuse → 401 (family revoked)")

        # ---- the rotated refresh is now dead too (nuclear revoke) ----
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
        assert r.status_code == 401, r.text
        print("[ok] rotated refresh also dead after reuse-detect")

        # ---- log back in for the logout leg ----
        r = client.post("/api/v1/auth/login",
                        json={"email": args.email, "password": args.password})
        assert r.status_code == 200, r.text
        access2 = r.json()["access_token"]

        # ---- /me works, /logout works, /me now fails ----
        r = client.get("/api/v1/auth/me",
                       headers={"Authorization": f"Bearer {access2}"})
        assert r.status_code == 200, r.text
        r = client.post("/api/v1/auth/logout",
                        headers={"Authorization": f"Bearer {access2}"})
        assert r.status_code == 204, r.text
        r = client.get("/api/v1/auth/me",
                       headers={"Authorization": f"Bearer {access2}"})
        assert r.status_code == 401, r.text
        print("[ok] logout → session revoked, /me now 401")

        # ---- /jwks is public ----
        r = client.get("/api/v1/auth/jwks")
        assert r.status_code == 200, r.text
        assert len(r.json()["keys"]) == 1
        print("[ok] /jwks public")

    print("[done]")


if __name__ == "__main__":
    main()
