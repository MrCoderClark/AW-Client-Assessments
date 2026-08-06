"""SessionService — refresh-family lifecycle in the sessions table.

Nuclear family-revoke on reuse for M1: if a refresh token that has
already been rotated shows up again, we revoke every session of that
user. That's the correct security posture (someone has the stolen
token), and simpler than tracking the family root via a recursive CTE.
M4 can refine to per-family revocation if operators find full-logouts
too disruptive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .random import new_id
from .settings import AuthSettings


@dataclass(frozen=True)
class SessionRow:
    id: str
    user_id: str
    parent_id: str | None
    revoked_at: datetime | None
    expires_at: datetime
    remember_me: bool


class SessionService:
    def __init__(self, settings: AuthSettings) -> None:
        self.s = settings

    def _ttl(self, remember_me: bool) -> int:
        return (
            self.s.refresh_remember_me_ttl_seconds
            if remember_me
            else self.s.refresh_token_ttl_seconds
        )

    async def create(
        self,
        conn: AsyncConnection,
        *,
        user_id: str,
        refresh_hash: str,
        user_agent: str,
        ip_address: str,
        remember_me: bool,
        parent_id: str | None = None,
    ) -> str:
        sid = new_id()
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._ttl(remember_me))
        await conn.execute(
            text("""
                INSERT INTO sessions
                  (id, user_id, refresh_token_hash, parent_id, user_agent,
                   ip_address, remember_me, expires_at, last_used_at, created_at)
                VALUES
                  (:id, :uid, :rh, :parent, :ua, :ip, :rm, :exp, :now, :now)
            """),
            {
                "id": sid, "uid": user_id, "rh": refresh_hash, "parent": parent_id,
                "ua": user_agent, "ip": ip_address, "rm": remember_me,
                "exp": expires, "now": now,
            },
        )
        return sid

    async def find_by_refresh_hash(
        self, conn: AsyncConnection, refresh_hash: str
    ) -> SessionRow | None:
        r = await conn.execute(
            text("""
                SELECT id, user_id, parent_id, revoked_at, expires_at, remember_me
                FROM sessions WHERE refresh_token_hash = :h
            """),
            {"h": refresh_hash},
        )
        row = r.first()
        if row is None:
            return None
        return SessionRow(
            id=str(row.id), user_id=str(row.user_id),
            parent_id=str(row.parent_id) if row.parent_id else None,
            revoked_at=row.revoked_at, expires_at=row.expires_at,
            remember_me=bool(row.remember_me),
        )

    async def rotate(
        self,
        conn: AsyncConnection,
        old_session_id: str,
        *,
        user_id: str,
        new_refresh_hash: str,
        user_agent: str,
        ip_address: str,
        remember_me: bool,
    ) -> str:
        """Revoke the old session and create a new one chained via parent_id.

        Returns the new session id.
        """
        await conn.execute(
            text("""
                UPDATE sessions
                SET revoked_at = now(), revoked_reason = 'rotated'
                WHERE id = :sid AND revoked_at IS NULL
            """),
            {"sid": old_session_id},
        )
        return await self.create(
            conn,
            user_id=user_id,
            refresh_hash=new_refresh_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            remember_me=remember_me,
            parent_id=old_session_id,
        )

    async def revoke(
        self, conn: AsyncConnection, session_id: str, reason: str
    ) -> None:
        await conn.execute(
            text("""
                UPDATE sessions
                SET revoked_at = now(), revoked_reason = :r
                WHERE id = :sid AND revoked_at IS NULL
            """),
            {"sid": session_id, "r": reason},
        )

    async def revoke_all_for_user(
        self, conn: AsyncConnection, user_id: str, reason: str
    ) -> int:
        r = await conn.execute(
            text("""
                UPDATE sessions
                SET revoked_at = now(), revoked_reason = :r
                WHERE user_id = :uid AND revoked_at IS NULL
            """),
            {"uid": user_id, "r": reason},
        )
        return r.rowcount or 0

    async def session_and_user_state(
        self, conn: AsyncConnection, session_id: str
    ) -> dict[str, Any] | None:
        """For access-token middleware: joined session+user state used to
        reject stale tokens (revoked session, or user.ver bumped)."""
        r = await conn.execute(
            text("""
                SELECT s.revoked_at, s.user_id, u.ver AS user_ver, u.role, u.status
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.id = :sid
            """),
            {"sid": session_id},
        )
        row = r.first()
        if row is None:
            return None
        return {
            "revoked_at": row.revoked_at,
            "user_id": str(row.user_id),
            "user_ver": int(row.user_ver),
            "role": str(row.role),
            "status": str(row.status),
        }
