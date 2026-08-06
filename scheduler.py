"""Background scheduler running inside the FastAPI process.

Ponytail: no APScheduler, no cron parser. One row in `schedule`, checked every
30s. Fires when: enabled, weekday matches, current time is past time_of_day,
and it hasn't already run today. Runs sync scan/commit generators in a thread.

Also drives the PC stale-unreachable alerter — see pc_health.check_stale_pcs.
That runs at most every 15 minutes; the alert itself dedupes per-PC via the
notifications table (~20h window), so this cadence just limits DB reads.
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from commit import commit_all
from db import connect, get_schedule, mark_schedule_run
from pc_health import check_stale_pcs
from scan import scan_all

CHECK_INTERVAL_SEC = 30
PC_HEALTH_CHECK_EVERY_SEC = 15 * 60   # 15 minutes
# ponytail: single source of truth for the scheduler's clock. All comparisons
# (weekday, time-of-day, "did we already run today") are in this zone; drop-in
# override via env if you ever redeploy to a different site.
import os as _os
LOCAL_TZ = ZoneInfo(_os.environ.get("APP_TIMEZONE", "America/New_York"))


def _now() -> datetime:
    return datetime.now(LOCAL_TZ)


def _parse_weekdays(csv: str) -> set[int]:
    if not csv:
        return set()
    return {int(x) for x in csv.split(",") if x.strip().isdigit()}


def _due_now(sched: dict, now: datetime) -> bool:
    if not sched.get("enabled"):
        return False
    weekdays = _parse_weekdays(sched.get("weekdays", ""))
    if now.weekday() not in weekdays:
        return False
    try:
        h, m = map(int, sched["time_of_day"].split(":"))
    except (ValueError, KeyError):
        return False
    if now.hour < h or (now.hour == h and now.minute < m):
        return False
    # Skip only if we've ALREADY fired today's slot. Comparing against today's
    # slot (not "any run today") means editing time_of_day after an earlier
    # run still leaves the new slot free to fire.
    # ponytail: previous version compared last_run_at.date() == now.date(),
    # which quietly ate a run when time_of_day was changed between runs.
    today_slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
    last = sched.get("last_run_at")
    if last:
        last_dt: datetime | None = None
        if isinstance(last, datetime):
            last_dt = last
        else:
            try:
                last_dt = datetime.fromisoformat(str(last))
            except ValueError:
                last_dt = None
        if last_dt is not None:
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=LOCAL_TZ)
            if last_dt.astimezone(LOCAL_TZ) >= today_slot:
                return False
    return True


def compute_next_run(sched: dict, from_time: datetime | None = None) -> str | None:
    """Return ISO timestamp (with America/New_York offset) of next scheduled run,
    or None if disabled/no valid config."""
    if not sched.get("enabled"):
        return None
    weekdays = _parse_weekdays(sched.get("weekdays", ""))
    if not weekdays:
        return None
    try:
        h, m = map(int, sched["time_of_day"].split(":"))
    except (ValueError, KeyError):
        return None
    now = from_time or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=LOCAL_TZ)
    for delta in range(0, 8):
        candidate = (now + timedelta(days=delta)).replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            continue
        if candidate.weekday() in weekdays:
            return candidate.isoformat(timespec="seconds")
    return None


def _run_mode(mode: str) -> bool:
    """Run scan (+ commit if mode='scan+commit'). Return True on no exception."""
    try:
        for _ in scan_all():
            pass
        if mode == "scan+commit":
            for _ in commit_all():
                pass
        return True
    except Exception as e:
        print(f"[scheduler] run failed: {e}")
        return False


async def scheduler_loop() -> None:
    print(f"[scheduler] loop started; timezone={LOCAL_TZ.key}")
    last_health_check: datetime | None = None
    while True:
        try:
            conn = connect()
            sched = get_schedule(conn)
            now = _now()
            if _due_now(sched, now):
                print(f"[scheduler] firing mode={sched['mode']} at {now:%Y-%m-%d %H:%M:%S %Z}")
                ok = await asyncio.to_thread(_run_mode, sched["mode"])
                mark_schedule_run(conn, ok)
                print(f"[scheduler] done, ok={ok}")

            # PC health alerter — every 15 minutes, dedupe is inside check_stale_pcs.
            if last_health_check is None or (now - last_health_check).total_seconds() >= PC_HEALTH_CHECK_EVERY_SEC:
                try:
                    found, alerted, note = await asyncio.to_thread(check_stale_pcs)
                    if found:
                        print(f"[pc_health] stale={found} alerted={alerted} · {note}")
                except Exception as e:
                    print(f"[pc_health] check failed: {e}")
                last_health_check = now
        except Exception as e:
            print(f"[scheduler] loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SEC)
