"""PC-health transition smoke.

Exercises upsert_pc_status's transition-return contract by simulating
the three cases scan.py emits on:
  1. New PC row → prior is None → no notification.
  2. Was reachable, now unreachable → prior is True → emit.
  3. Was unreachable, now reachable → prior is False → emit.
  4. Repeat same state → no re-emit.

The emit itself is verified by counting rows in `notifications` for the
admin. Uses `emit_sync` in-thread so no event loop concerns.

Run:  uv run --env-file .env python scripts/pc_health_smoke.py \
        --admin-email admin@aw.local
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect as sync_connect, upsert_pc_status


def _cleanup(pc_name: str) -> None:
    conn = sync_connect()
    conn.execute("DELETE FROM pc_status WHERE pc_name = %s", (pc_name,))
    conn.execute(
        "DELETE FROM notifications WHERE context_json ->> 'pc_name' = %s",
        (pc_name,),
    )
    conn.commit()
    conn.close()


def _count_notifications(admin_email: str, pc_name: str) -> int:
    conn = sync_connect()
    r = conn.execute(
        """SELECT COUNT(*) AS n
             FROM notifications n JOIN users u ON u.id = n.user_id
             WHERE u.email_normalized = %s
               AND n.category = 'pc_health'
               AND n.context_json ->> 'pc_name' = %s""",
        (admin_email.lower(), pc_name),
    ).fetchone()
    conn.close()
    return int(r["n"])


def _wait_for(fn, timeout=3.0):
    """emit_sync uses a background thread — poll briefly for the row."""
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", required=True)
    args = ap.parse_args()

    pc = f"SMOKE-PC-{int(time.time())}"
    host = "10.0.0.99"
    _cleanup(pc)

    from scan import _notify_pc_transition  # emit helper

    conn = sync_connect()

    # ---- 1. first-seen (no prior row) → no notification ---------
    prior = upsert_pc_status(conn, pc, host, reachable=True)
    assert prior is None, f"first insert should return None, got {prior}"
    # no notification for first-seen (scan.py checks prior is False/True explicitly)
    assert _count_notifications(args.admin_email, pc) == 0
    print(f"[ok] first-seen {pc}: prior=None, no notification")

    # ---- 2. same state again → prior=True → no notification ----
    prior = upsert_pc_status(conn, pc, host, reachable=True)
    assert prior is True
    # scan.py won't emit because prior matches new state; simulate that gate here
    assert _count_notifications(args.admin_email, pc) == 0
    print("[ok] no-op stays quiet (prior=True, still reachable)")

    # ---- 3. reachable → unreachable → prior=True → emit --------
    prior = upsert_pc_status(conn, pc, host, reachable=False, error="ConnectionRefusedError: [Errno 111]")
    assert prior is True
    _notify_pc_transition(pc, host, now_reachable=False, error="ConnectionRefusedError: [Errno 111]")
    assert _wait_for(lambda: _count_notifications(args.admin_email, pc) == 1), "unreachable emit did not land"
    print("[ok] reachable→unreachable emitted pc_unreachable")

    # ---- 4. unreachable → reachable → prior=False → emit -------
    prior = upsert_pc_status(conn, pc, host, reachable=True)
    assert prior is False
    _notify_pc_transition(pc, host, now_reachable=True)
    assert _wait_for(lambda: _count_notifications(args.admin_email, pc) == 2), "reachable emit did not land"
    print("[ok] unreachable→reachable emitted pc_reachable")

    conn.close()
    _cleanup(pc)
    print("[done]")


if __name__ == "__main__":
    main()
