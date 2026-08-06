"""RBAC smoke: anonymous is rejected, admin bearer works, /health stays public.

Run:  uv run --env-file .env python scripts/rbac_smoke.py \
        --email admin@aw.local --password 'Correct-Horse-Battery-9!'
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from api import app

# (method, path) → expected result for anonymous vs admin
# body_401 == True → anonymous should get 401 problem+json
# body_ok  == expected admin status (200 for read, or another expected)
PROBE = [
    ("GET",    "/api/health",           False),  # public
    ("GET",    "/api/pdfs",             True),
    ("GET",    "/api/pcs",              True),
    ("GET",    "/api/logs",             True),
    ("GET",    "/api/runs",             True),
    ("GET",    "/api/schedule",         True),
    ("GET",    "/api/pcs/PC1/browse",   True),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    with TestClient(app) as client:
        # ---- Anonymous ----
        for method, path, needs_auth in PROBE:
            r = client.request(method, path)
            if not needs_auth:
                assert r.status_code == 200, f"{path} public → expected 200, got {r.status_code}"
                print(f"[ok] anon  {method:6} {path}  → 200 (public)")
                continue
            assert r.status_code == 401, f"{path} anon → expected 401, got {r.status_code}: {r.text[:200]}"
            body = r.json()
            assert body["code"] == "AUTH_UNAUTHENTICATED", f"{path} → {body}"
            assert r.headers["content-type"].startswith("application/problem+json")
            print(f"[ok] anon  {method:6} {path}  → 401 problem+json")

        # ---- Admin bearer ----
        r = client.post("/api/v1/auth/login",
                        json={"email": args.email, "password": args.password})
        assert r.status_code == 200, r.text
        access = r.json()["access_token"]
        auth = {"Authorization": f"Bearer {access}"}

        for method, path, needs_auth in PROBE:
            r = client.request(method, path, headers=auth)
            # 200 or a domain error is fine (browse for a real PC can 400 if
            # SMB is unreachable in this env); what we care about is NOT 401/403.
            assert r.status_code not in (401, 403), \
                f"admin {method} {path} → {r.status_code}: {r.text[:200]}"
            print(f"[ok] admin {method:6} {path}  → {r.status_code}")

        # ---- Schedule PUT with valid body ----
        r = client.put("/api/schedule",
                       json={"enabled": True, "mode": "scan", "time_of_day": "05:00", "weekdays": "1,2,3,4,5"},
                       headers=auth)
        assert r.status_code not in (401, 403), r.text
        print(f"[ok] admin PUT    /api/schedule  → {r.status_code}")

        # ---- Bulk endpoint: inline permission check ----
        # Admin has both run:trigger AND pdf:delete, so both actions accepted.
        # We just probe with an empty ids list expecting 400 (auth passed, business
        # rule failed — proves the dep chain worked).
        r = client.post("/api/pdfs/bulk",
                        json={"action": "commit", "ids": []}, headers=auth)
        assert r.status_code == 400, r.text
        print("[ok] admin POST   /api/pdfs/bulk commit → 400 (empty ids; auth passed)")

        r = client.post("/api/pdfs/bulk",
                        json={"action": "delete", "ids": []}, headers=auth)
        assert r.status_code == 400, r.text
        print("[ok] admin POST   /api/pdfs/bulk delete → 400 (empty ids; auth passed)")

        # ---- Bulk endpoint anonymous → some 4xx rejection.
        # After branch 28 the CsrfMiddleware sits outside auth, so an anon
        # POST without a Bearer OR CSRF cookie now fails CSRF (403) instead
        # of auth (401). Both are correct rejections — fail-fast is the
        # intended design.
        r = client.post("/api/pdfs/bulk", json={"action": "commit", "ids": []})
        assert r.status_code in (401, 403), r.text
        print(f"[ok] anon  POST   /api/pdfs/bulk        → {r.status_code}")

    print("[done]")


if __name__ == "__main__":
    main()
