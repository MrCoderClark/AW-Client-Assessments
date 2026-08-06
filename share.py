r"""Shared SMB helpers for the destination share (Client_Assessments).

Anything that reads the share's top-level layout — backfill, the archive
service, the (future) repair script — should go through
`list_active_date_folders()` so the archive tree can never leak into an
active-content walk by accident.
"""
from __future__ import annotations

import re
import time

import smbclient

ARCHIVE_FOLDER_NAME = "_Archive"
DATED_FOLDER = re.compile(r"^\d{2}-\d{2}-\d{4}$")


def list_active_date_folders(share: str) -> list[str]:
    """Sorted MM-DD-YYYY subfolder names under `share`, minus `_Archive/`.

    The regex alone would already exclude `_Archive/`, but we skip it by
    name explicitly so a future rename of the archive root can't silently
    start feeding archived files back into active scans.
    """
    out: list[str] = []
    for entry in smbclient.scandir(share):
        if not entry.is_dir():
            continue
        name = entry.name
        if name == ARCHIVE_FOLDER_NAME:
            continue
        if DATED_FOLDER.match(name):
            out.append(name)
    return sorted(out)


def smb_rename_with_retry(src: str, dst: str, max_attempts: int = 5) -> None:
    """SMB rename with exponential backoff on transient credit exhaustion.

    SMB2 uses credit-based flow control; concurrent workers on one session
    can outrun the server's grant rate, surfacing as
    STATUS_INSUFFICIENT_RESOURCES or 'credits' errors. Mirrors the retry
    logic in `scripts/backfill_share._read_pdf_bytes`.
    """
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            smbclient.rename(src, dst)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e).lower()
            transient = (
                "credits" in msg
                or "status_insufficient_resources" in msg
            )
            if not transient or attempt == max_attempts - 1:
                raise
            time.sleep(0.1 * (2 ** attempt))  # 0.1, 0.2, 0.4, 0.8, 1.6
    if last_err is not None:
        raise last_err
