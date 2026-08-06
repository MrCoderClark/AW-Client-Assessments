"""Commit-report email. Uses the shared HTML template engine in auth/emails.py.

Reads SMTP creds from env. If anything's missing or send fails, we log and move
on — never fail the commit because email had a bad day.
"""
import os

from auth.emails import commit_report_email, send_mail
from db import connect


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def _config_ok() -> tuple[bool, list[str]]:
    missing = [k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "EMAIL_FROM", "EMAIL_TO") if not _env(k)]
    return (not missing, missing)


def _load_committed_files(started_at: str, ended_at: str) -> tuple[list[dict], list[str]]:
    conn = connect()
    rows = conn.execute(
        """
        SELECT host, filename, proposed_name, dest_path, size
        FROM pdfs
        WHERE committed_at IS NOT NULL
          AND committed_at BETWEEN %s AND %s
        ORDER BY host, proposed_name
        """,
        (started_at, ended_at),
    ).fetchall()
    files = [dict(r) for r in rows]
    dest_folders = sorted({r["dest_path"].rsplit("\\", 1)[0] for r in rows if r["dest_path"]})
    return files, dest_folders


def send_commit_report(run_id: int, counts: dict, started_at: str, ended_at: str) -> tuple[bool, str]:
    """Send report. Returns (ok, message)."""
    ok_env, missing = _config_ok()
    if not ok_env:
        return False, f"email skipped: missing env vars: {', '.join(missing)}"

    email_to = [t.strip() for t in (_env("EMAIL_TO") or "").split(",") if t.strip()]
    if not email_to:
        return False, "email skipped: EMAIL_TO empty"

    files, dest_folders = _load_committed_files(started_at, ended_at)
    spec = commit_report_email(
        run_id=run_id, counts=counts,
        started_at=started_at, ended_at=ended_at,
        files=files, dest_folders=dest_folders,
    )
    last_result = (False, "no recipients")
    for addr in email_to:
        last_result = send_mail(addr, spec)
    return last_result
