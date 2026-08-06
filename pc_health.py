"""Stale-PC alerter.

Once per scheduler tick, looks for PCs that have been unreachable (or never
seen) for at least PC_HEALTH_STALE_DAYS days and haven't already been
alerted about in the last ~20 hours. Emits a `pc_health` in-app
notification to admins/operators and sends one alert email covering all
currently-stale PCs to EMAIL_TO.

Dedupe uses the notifications table itself — no schema change. Each stale
PC gets one notification per ~day; the summary email is likewise gated so
recipients don't get spammed if the scheduler runs every 30s.
"""
from __future__ import annotations

import os

from auth.emails import pc_unreachable_alert_email, send_mail, app_base_url
from auth.notifications import NotificationEvent, emit_sync
from db import connect


def _stale_days() -> int:
    try:
        return max(1, int(os.environ.get("PC_HEALTH_STALE_DAYS", "3")))
    except ValueError:
        return 3


def _email_to() -> list[str]:
    raw = os.environ.get("EMAIL_TO", "") or ""
    return [t.strip() for t in raw.split(",") if t.strip()]


def _find_stale_pcs(conn, days: int) -> list[dict]:
    """PCs marked unreachable (or never-scanned) whose last-anything is older
    than `days` days."""
    threshold = f"{days} days"
    rows = conn.execute(
        """
        SELECT pc_name, host, last_seen, last_attempt, last_reachable, last_error
        FROM pc_status
        WHERE (last_reachable = false OR last_reachable IS NULL)
          AND (
            (last_seen IS NOT NULL AND last_seen < now() - CAST(%s AS interval))
            OR (last_seen IS NULL AND last_attempt IS NOT NULL
                AND last_attempt < now() - CAST(%s AS interval))
          )
        ORDER BY pc_name
        """,
        (threshold, threshold),
    ).fetchall()
    return [dict(r) for r in rows]


def _already_alerted(conn, pc_name: str, hours: int = 20) -> bool:
    r = conn.execute(
        """
        SELECT 1 FROM notifications
        WHERE category = 'pc_health'
          AND kind = 'pc_unreachable_stale'
          AND context_json ->> 'pc_name' = %s
          AND created_at > now() - CAST(%s AS interval)
        LIMIT 1
        """,
        (pc_name, f"{hours} hours"),
    ).fetchone()
    return r is not None


def check_stale_pcs() -> tuple[int, int, str]:
    """Run the stale-PC check. Returns (found, newly_alerted, note).

    Idempotent within the dedupe window — safe to call every scheduler
    tick. Silent no-op when no stale PCs.
    """
    days = _stale_days()
    conn = connect()
    stale = _find_stale_pcs(conn, days)
    if not stale:
        return (0, 0, "no stale PCs")

    to_alert: list[dict] = []
    for pc in stale:
        if _already_alerted(conn, pc["pc_name"]):
            continue
        to_alert.append(pc)
        emit_sync(NotificationEvent(
            category="pc_health",
            kind="pc_unreachable_stale",
            severity="WARN",
            title=f"{pc['pc_name']} offline {days}+ days",
            body=(f"{pc['pc_name']} ({pc['host']}) hasn't been reachable since "
                  f"{pc['last_seen'] or 'first scan'}. "
                  f"Last error: {pc['last_error'] or 'n/a'}"),
            url=f"/pcs?open={pc['pc_name']}",
            context={"pc_name": pc["pc_name"], "host": pc["host"],
                     "stale_days": days},
        ))

    if not to_alert:
        return (len(stale), 0, "all stale PCs already alerted in the last 20h")

    # Send one summary email covering everything newly alerted this pass.
    to_addrs = _email_to()
    if not to_addrs:
        return (len(stale), len(to_alert), "notif emitted; EMAIL_TO empty — no email")

    dashboard_url = f"{app_base_url().rstrip('/')}/pcs"
    spec = pc_unreachable_alert_email(
        pcs=to_alert, stale_days=days, dashboard_url=dashboard_url,
    )
    results = [send_mail(addr, spec) for addr in to_addrs]
    ok = sum(1 for r in results if r[0])
    return (len(stale), len(to_alert), f"emailed {ok}/{len(to_addrs)} recipient(s)")
