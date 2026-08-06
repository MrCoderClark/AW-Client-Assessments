"""Admin-side invite: create a user + invitation via the API endpoint.

Runs against the API (not the DB directly) so the same flow the admin UI
uses is exercised. Requires an existing admin's credentials.

Run:
  uv run --env-file .env python scripts/invite_user.py \
      --admin-email admin@aw.local --admin-password 'Correct-Horse-Battery-9!' \
      --email newoperator@aw.local --role operator \
      --first "Jane" --last "Doe"

Prints the invite URL for out-of-band delivery when SMTP isn't configured.
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
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--role", default="viewer", choices=["admin", "operator", "viewer"])
    ap.add_argument("--first", default="")
    ap.add_argument("--last", default="")
    args = ap.parse_args()

    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login",
                   json={"email": args.admin_email, "password": args.admin_password})
        if r.status_code != 200:
            sys.exit(f"[fail] admin login: {r.status_code} {r.text}")
        access = r.json()["access_token"]

        r = c.post("/api/v1/auth/users",
                   headers={"Authorization": f"Bearer {access}"},
                   json={
                       "email": args.email, "role": args.role,
                       "first_name": args.first, "last_name": args.last,
                   })
        if r.status_code != 201:
            sys.exit(f"[fail] invite: {r.status_code} {r.text}")
        body = r.json()
        print(f"[ok] invited {args.email} as {args.role}")
        print(f"     user_id:    {body['user_id']}")
        print(f"     invite_url: {body['invite_url']}")
        print(f"     mail:       {'sent' if body['mail_ok'] else 'FAILED'} — {body['mail_note']}")
        if not body["mail_ok"]:
            print()
            print("    ↑ SMTP send failed. Deliver the URL above out-of-band")
            print("      (Slack, encrypted DM). It's single-use and expires in 7 days.")


if __name__ == "__main__":
    main()
