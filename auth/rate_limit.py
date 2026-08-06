"""Rate limiter over `rate_limit_buckets`.

Fixed-window buckets keyed by `{category}:{key}:{bucket_start_epoch}`.
The window is split into `_SUB_BUCKETS` sub-buckets so worst-case burst
at a window seam is bounded by (1/N * limit) rather than 2x.

ponytail: fixed sub-windows instead of a true sliding weighted-edge
window — accurate enough for auth flows. Upgrade to weighted-edge only
if abuse is observed.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .audit import AuditEvent, audit_logger
from .context import ANONYMOUS, AuthContext

_SUB_BUCKETS = 4


async def check_and_consume(
    *,
    category: str,
    key: str,
    limit: int,
    window_seconds: int,
    request_id: str = "",
    ip_address: str | None = None,
    user_agent: str | None = None,
    user_id: str | None = None,
) -> int | None:
    """Returns None if the action is allowed (one slot consumed).
    Returns retry-after seconds if the limit is exceeded (no slot consumed).

    Emits a `SEC_RATE_LIMITED` audit on breach.

    Runs in its own committed transaction so the counter survives even when
    the caller's request tx rolls back on an authentication failure — that
    is the exact case where accurate rate-limit counting matters.
    """
    from .engine import transaction

    bucket_size = max(1, window_seconds // _SUB_BUCKETS)
    now = int(time.time())
    bucket_start = (now // bucket_size) * bucket_size
    bucket_key = f"{category}:{key}:{bucket_start}"
    expires = datetime.fromtimestamp(bucket_start + window_seconds, tz=UTC)
    prefix = f"{category}:{key}:"

    async with transaction() as own_conn:
        used = int(
            (await own_conn.execute(
                text("""
                    SELECT COALESCE(SUM(count), 0)
                    FROM rate_limit_buckets
                    WHERE bucket_key LIKE :p AND expires_at > now()
                """),
                {"p": prefix + "%"},
            )).scalar() or 0
        )

        if used >= limit:
            earliest = (await own_conn.execute(
                text("""
                    SELECT MIN(expires_at) FROM rate_limit_buckets
                    WHERE bucket_key LIKE :p AND expires_at > now()
                """),
                {"p": prefix + "%"},
            )).scalar()
            retry_after = window_seconds
            if earliest is not None:
                retry_after = max(1, int((earliest - datetime.now(UTC)).total_seconds()))
            ctx = AuthContext(
                actor_kind="user" if user_id else "anonymous",
                user_id=user_id,
                request_id=request_id,
            )
            await audit_logger().emit(
                None, ctx,
                AuditEvent(
                    action="SEC_RATE_LIMITED", outcome="denied", severity="SEC",
                    context={"category": category, "key": key, "limit": limit,
                             "window_seconds": window_seconds},
                ),
                actor_ip=ip_address, user_agent=user_agent,
            )
            return retry_after

        await own_conn.execute(
            text("""
                INSERT INTO rate_limit_buckets (bucket_key, count, expires_at)
                VALUES (:k, 1, :exp)
                ON CONFLICT (bucket_key) DO UPDATE
                  SET count = rate_limit_buckets.count + 1
            """),
            {"k": bucket_key, "exp": expires},
        )
    return None


async def sweep_expired(conn: AsyncConnection) -> int:
    """Delete expired buckets. Call from a periodic cleanup. Returns rows removed.

    ponytail: no scheduled sweep wired yet — table stays tiny under real
    traffic. Add a nightly job if it grows past a few thousand rows.
    """
    r = await conn.execute(
        text("DELETE FROM rate_limit_buckets WHERE expires_at <= now()")
    )
    return r.rowcount or 0
