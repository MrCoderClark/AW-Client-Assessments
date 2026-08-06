"""Index PDFs on every PC's Client profile into SQLite.

Non-destructive: never copies, never deletes. Just records what exists.
Skips files unchanged since last scan (path, size, mtime match).

Callable as CLI (uv run scan.py) or as a generator from api.py.
"""
import hashlib
import io
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Iterator

import smbclient
from pypdf import PdfReader

from classify import detect_assessment, detect_name, proposed_filename
from db import connect, existing_fingerprint, finish_run, start_run, upsert, upsert_pc_status
from pcs import PCS
from unlock import kill_pdf_readers

USER_PROFILE = "Client"
FOLDERS = ("Desktop", "Documents", "Downloads")
CONNECT_TIMEOUT = 5
UNLOCK_WAIT = 1.0


def text_hash(text: str | None) -> str | None:
    """SHA-256 of extracted PDF text with whitespace normalized.

    Used to dedupe byte-different but content-identical PDFs — the vendor
    embeds a fresh /CreationDate/ID on every download, so raw MD5 misses
    the duplicate.
    """
    if not text:
        return None
    normalized = " ".join(text.split()).lower()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _creds() -> tuple[str, str]:
    u, p = os.environ.get("SMB_USER"), os.environ.get("SMB_PASS")
    if not (u and p):
        raise RuntimeError("SMB_USER and SMB_PASS must be set")
    return u, p


def current_week_range() -> tuple[datetime, datetime]:
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, monday + timedelta(days=4, hours=23, minutes=59, seconds=59)


def _is_sharing_violation(err: Exception) -> bool:
    s = str(err).lower()
    return "0xc0000043" in s or "used by another process" in s


def _read_pdf_bytes(host: str, path: str, user: str, password: str) -> tuple[bytes, str | None]:
    """Read a PDF over SMB. If locked, try to unlock and retry once.
    Returns (bytes, unlock_note) — unlock_note is a printable string if we killed something.
    """
    try:
        with smbclient.open_file(path, mode="rb") as f:
            return f.read(), None
    except Exception as e:
        if not _is_sharing_violation(e):
            raise
        killed = kill_pdf_readers(host, user, password)
        note = f"killed {killed or 'nothing'}"
        time.sleep(UNLOCK_WAIT)
        with smbclient.open_file(path, mode="rb") as f:
            return f.read(), note


def _walk_pdfs(root: str):
    try:
        for entry in smbclient.scandir(root):
            if entry.is_dir():
                yield from _walk_pdfs(f"{root}\\{entry.name}")
            elif entry.name.lower().endswith(".pdf"):
                yield f"{root}\\{entry.name}", entry
    except OSError:
        return


def _scan_pc(pc_name: str, host: str, conn, week_start, week_end, user: str, password: str) -> Iterator[str]:  # noqa: PLR0913

    """Yield log lines while scanning one PC. Returns nothing; side-effect is DB writes."""
    smbclient.register_session(host, username=user, password=password, connection_timeout=CONNECT_TIMEOUT)
    base = rf"\\{host}\C$\Users\{USER_PROFILE}"
    counts = {"seen": 0, "new": 0, "updated": 0, "unchanged": 0, "noassess": 0, "locked": 0}

    for folder in FOLDERS:
        root = rf"{base}\{folder}"
        try:
            smbclient.stat(root)
        except OSError:
            continue

        for path, entry in _walk_pdfs(root):
            st = entry.stat()
            mtime = datetime.fromtimestamp(st.st_mtime)
            if not (week_start <= mtime <= week_end):
                continue
            counts["seen"] += 1

            fingerprint = existing_fingerprint(conn, host, path)
            if fingerprint == (st.st_size, mtime.isoformat()):
                counts["unchanged"] += 1
                continue

            try:
                data, unlock_note = _read_pdf_bytes(host, path, user, password)
            except Exception as e:
                counts["locked"] += 1
                yield f"    [locked-skip] {os.path.basename(path)}: {e.__class__.__name__}"
                continue

            if unlock_note:
                yield f"    [unlock] {host}: {unlock_note} — retried read"

            md5 = hashlib.md5(data).hexdigest()
            try:
                reader = PdfReader(io.BytesIO(data))
                text = "\n".join((p.extract_text() or "") for p in reader.pages[:3])
            except Exception as e:
                text = ""
                yield f"    [parse-warn] {os.path.basename(path)}: {e}"

            fname = os.path.basename(path)
            assessment = detect_assessment(text, fname)
            if not assessment:
                counts["noassess"] += 1
                yield f"    [skip-noassess] {fname}"
                continue

            name = detect_name(text, fname)
            first, last = name if name else (None, None)
            new_name = proposed_filename(text, fname)

            action = "new" if fingerprint is None else "update"
            counts["new" if fingerprint is None else "updated"] += 1

            upsert(
                conn, host=host, source_path=path, filename=fname,
                proposed_name=new_name, assessment_type=assessment,
                first_name=first, last_name=last, size=st.st_size,
                mtime=mtime.isoformat(), md5=md5, text_hash=text_hash(text),
            )
            yield f"    [{action}] {fname}  →  {new_name}"

    conn.commit()
    prior = upsert_pc_status(conn, pc_name, host, reachable=True, counts=counts)
    if prior is False:
        _notify_pc_transition(pc_name, host, now_reachable=True)
    if counts["seen"] == 0:
        yield "    (no PDFs in window)"
    yield f"    · seen={counts['seen']} new={counts['new']} updated={counts['updated']} unchanged={counts['unchanged']} noassess={counts['noassess']} locked={counts['locked']}"


def _notify_pc_transition(pc_name: str, host: str, *, now_reachable: bool, error: str | None = None) -> None:
    """Fire-and-forget notification when a PC crosses the reachability line.
    Only called on transition, so no dedupe needed — no spam if a PC has
    been down for many scans."""
    from auth.notifications import NotificationEvent, emit_sync
    if now_reachable:
        emit_sync(NotificationEvent(
            category="pc_health", kind="pc_reachable", severity="INFO",
            title=f"{pc_name} back online",
            body=f"{pc_name} ({host}) is reachable again.",
            url="/pcs",
            context={"pc_name": pc_name, "host": host},
        ))
    else:
        emit_sync(NotificationEvent(
            category="pc_health", kind="pc_unreachable", severity="WARN",
            title=f"{pc_name} is unreachable",
            body=(error or "").splitlines()[0] if error else f"{pc_name} ({host}) went offline.",
            url="/pcs",
            context={"pc_name": pc_name, "host": host, "error": error},
        ))


def scan_all() -> Iterator[str]:
    """Full scan across all PCs, yielding log lines. Safe to consume from CLI or API."""
    user, password = _creds()
    week_start, week_end = current_week_range()
    yield f"Window: {week_start:%Y-%m-%d} → {week_end:%Y-%m-%d}"

    conn = connect()
    run_id = start_run(conn, "scan")
    reachable = unreachable = 0
    for pc_name, host in PCS.items():
        yield f"{pc_name} ({host}):"
        try:
            yield from _scan_pc(pc_name, host, conn, week_start, week_end, user, password)
            reachable += 1
        except Exception as e:  # ponytail: broad catch — one bad PC must not stall the batch
            unreachable += 1
            err = f"{e.__class__.__name__}: {e}"
            prior = upsert_pc_status(conn, pc_name, host, reachable=False, error=err)
            if prior is True:
                _notify_pc_transition(pc_name, host, now_reachable=False, error=err)
            yield f"    [unreachable] {err}"

    finish_run(conn, run_id, {"reachable": reachable, "unreachable": unreachable, "total_pcs": len(PCS)})
    yield f"PCs: {reachable} reachable, {unreachable} unreachable"


def main() -> None:
    for line in scan_all():
        print(line)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(str(e))
