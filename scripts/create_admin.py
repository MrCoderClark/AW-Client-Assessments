"""Create or reset the bootstrap admin user.

Usage:
  uv run --env-file .env python scripts/create_admin.py \
      --email admin@example.com --password 'Correct-Horse-Battery-Staple-9!'

Idempotent: if the email exists, updates password + role + status. Otherwise
inserts a new ACTIVE admin. Prints the user id on success.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from auth.engine import dispose, transaction
from auth.passwords import PasswordComplexityError, PasswordService
from auth.random import new_id
from auth.settings import load


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--first-name", default="Bootstrap")
    ap.add_argument("--last-name", default="Admin")
    args = ap.parse_args()

    s = load()
    pw = PasswordService(s)
    try:
        pw.check_complexity(args.password)
    except PasswordComplexityError as e:
        sys.exit(f"[fail] {e}")

    email = args.email.strip()
    norm = email.lower()
    hashed = pw.hash(args.password)

    async with transaction() as conn:
        row = await conn.execute(
            text("SELECT id FROM users WHERE email_normalized = :e"), {"e": norm}
        )
        existing = row.first()
        if existing:
            uid = str(existing.id)
            await conn.execute(
                text("""
                    UPDATE users
                    SET password_hash = :h,
                        password_updated_at = now(),
                        role = 'admin',
                        status = 'ACTIVE',
                        email_verified_at = COALESCE(email_verified_at, now()),
                        must_change_password = false,
                        locked_until = NULL,
                        failed_login_attempts = 0,
                        ver = ver + 1,
                        updated_at = now(),
                        first_name = :fn,
                        last_name = :ln
                    WHERE id = :id
                """),
                {"h": hashed, "id": uid, "fn": args.first_name, "ln": args.last_name},
            )
            # Roll all existing sessions (ver bump would kill their tokens anyway).
            await conn.execute(
                text("""
                    UPDATE sessions SET revoked_at = now(), revoked_reason = 'admin_reset'
                    WHERE user_id = :id AND revoked_at IS NULL
                """),
                {"id": uid},
            )
            print(f"[updated] {email} → {uid}")
        else:
            uid = new_id()
            await conn.execute(
                text("""
                    INSERT INTO users
                      (id, email, email_normalized, email_verified_at,
                       first_name, last_name, display_name,
                       role, status, password_hash, password_updated_at,
                       must_change_password, ver, failed_login_attempts,
                       created_at, updated_at)
                    VALUES
                      (:id, :em, :norm, now(),
                       :fn, :ln, :dn,
                       'admin', 'ACTIVE', :h, now(),
                       false, 1, 0,
                       now(), now())
                """),
                {
                    "id": uid, "em": email, "norm": norm,
                    "fn": args.first_name, "ln": args.last_name,
                    "dn": f"{args.first_name} {args.last_name}".strip(),
                    "h": hashed,
                },
            )
            print(f"[new] {email} → {uid}")

    await dispose()


if __name__ == "__main__":
    asyncio.run(main())
