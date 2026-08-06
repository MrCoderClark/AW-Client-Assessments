"""DB-side of token verification: jti revocation + session lookup.

Kept out of tokens.py so that module stays dependency-free (pure crypto).
Middleware calls verify_access() first (fast), then these helpers (one
DB hit each; cached in M2 to hit p99 targets).
"""
from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def is_jti_revoked(conn: AsyncConnection, jti: str) -> bool:
    row = await conn.execute(
        text("SELECT 1 FROM access_token_revocations WHERE jti = :jti AND expires_at > now()"),
        {"jti": jti},
    )
    return row.first() is not None


async def revoke_jti(conn: AsyncConnection, jti: str, expires_at_epoch: int) -> None:
    """Add the jti to the revocation set. `expires_at_epoch` is the token's `exp`
    — the row can be pruned after that (no security value once expired anyway).
    """
    from datetime import UTC, datetime
    exp = datetime.fromtimestamp(expires_at_epoch, tz=UTC)
    await conn.execute(
        text(
            "INSERT INTO access_token_revocations (jti, expires_at) "
            "VALUES (:jti, :exp) ON CONFLICT (jti) DO NOTHING"
        ),
        {"jti": jti, "exp": exp},
    )


async def prune_revocations(conn: AsyncConnection) -> int:
    """Drop expired jti rows. Called by a background sweeper."""
    r = await conn.execute(text("DELETE FROM access_token_revocations WHERE expires_at <= now()"))
    return r.rowcount or 0


async def session_state(conn: AsyncConnection, session_id: str) -> dict | None:
    """Return {revoked_at, user_id, user_ver} or None if the session doesn't exist.

    A None return, a `revoked_at` value, or a `ver` != token.ver all mean
    'reject this access token'.
    """
    r = await conn.execute(
        text(
            "SELECT s.revoked_at, s.user_id, u.ver AS user_ver "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.id = :sid"
        ),
        {"sid": session_id},
    )
    row = r.first()
    if row is None:
        return None
    return {
        "revoked_at": row.revoked_at,
        "user_id": row.user_id,
        "user_ver": row.user_ver,
    }


def epoch_seconds() -> int:
    return int(time.time())
