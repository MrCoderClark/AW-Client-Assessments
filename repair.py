r"""Reconcile pdfs.archived_at / archive_path / archive_status against the
actual file locations on the destination share.

Six state kinds returned by _classify:

  ok                        — DB agrees with disk
  stale_active_row          — row says active, file is already at archive_path
  stale_archive_row         — row says archived, file is back at dest_path
  lost                      — row says archived, file gone from both paths
  both_exist                — file present at BOTH paths (crash mid-rename)
  active_missing            — active row, file missing at dest_path
  active_stale_archive_copy — active row + a leftover copy under _Archive/

Auto-fixable: stale_active_row, stale_archive_row, lost, both_exist.
Reported-only: active_missing, active_stale_archive_copy — the operator
decides whether to delete the row or the leftover file.

Callable as a library (api.py wires /api/pdfs/repair-check + /repair-apply
straight to `scan_all()`) or through the thin `scripts/repair_archive_state.py`
CLI wrapper.
"""
from __future__ import annotations

from typing import Any

import smbclient

from archive import _dest_to_archive_path, _register_dest_session
from auth import audit as audit_mod
from db import connect


def _smb_exists(path: str | None) -> bool:
    if not path:
        return False
    try:
        smbclient.stat(path)
        return True
    except OSError:
        return False


def _classify(row: dict) -> str:
    dest = row["dest_path"]
    apath = row["archive_path"]
    is_archived = row["archived_at"] is not None

    dest_here = _smb_exists(dest)
    apath_here = _smb_exists(apath)

    if not is_archived:
        if dest_here:
            return "active_stale_archive_copy" if apath_here else "ok"
        derived = _dest_to_archive_path(dest or "") if dest else None
        if derived and _smb_exists(derived):
            return "stale_active_row"
        return "active_missing"

    if apath_here and not dest_here:
        return "ok"
    if not apath_here and dest_here:
        return "stale_archive_row"
    if not apath_here and not dest_here:
        return "lost"
    return "both_exist"


def _fix_stale_active_row(conn, row: dict) -> None:
    dest = row["dest_path"]
    derived = _dest_to_archive_path(dest or "") if dest else None
    if not derived:
        return
    conn.execute(
        "UPDATE pdfs SET archived_at = now(), archive_path = %s, "
        "archive_status = 'archived' WHERE id = %s AND archived_at IS NULL",
        (derived, row["id"]),
    )
    conn.commit()


def _fix_stale_archive_row(conn, row: dict) -> None:
    conn.execute(
        "UPDATE pdfs SET archived_at = NULL, archive_path = NULL, "
        "archive_status = NULL WHERE id = %s AND archived_at IS NOT NULL",
        (row["id"],),
    )
    conn.commit()


def _fix_lost(conn, row: dict) -> None:
    conn.execute(
        "UPDATE pdfs SET archive_status = 'lost' WHERE id = %s "
        "AND archived_at IS NOT NULL",
        (row["id"],),
    )
    conn.commit()


def _fix_both_exist(conn, row: dict) -> None:
    """Trust the DB direction. Delete the duplicate at whichever path
    contradicts the stated state."""
    to_delete = row["dest_path"] if row["archived_at"] else row["archive_path"]
    if not to_delete:
        return
    try:
        smbclient.remove(to_delete)
    except OSError:
        pass  # already gone — fine


def scan_all(
    fix: bool = False,
    only_ids: list[int] | None = None,
    actor_id: str | None = None,
) -> dict[str, Any]:
    """Reconcile every row that has any archive column populated."""
    _register_dest_session()

    where = ["(archived_at IS NOT NULL OR archive_path IS NOT NULL "
             "OR archive_status IS NOT NULL)"]
    params: list = []
    if only_ids:
        placeholders = ",".join(["%s"] * len(only_ids))
        where.append(f"id IN ({placeholders})")
        params.extend(only_ids)

    conn = connect()
    try:
        rows = list(conn.execute(
            f"SELECT id, dest_path, archive_path, archived_at, archive_status "
            f"FROM pdfs WHERE {' AND '.join(where)} ORDER BY id",
            tuple(params),
        ))
    finally:
        conn.close()

    counts: dict[str, int] = {
        "ok": 0, "stale_active_row": 0, "stale_archive_row": 0,
        "lost": 0, "both_exist": 0, "active_missing": 0,
        "active_stale_archive_copy": 0,
    }
    fixed_ids: list[int] = []
    details: list[dict] = []

    conn = connect()
    try:
        for row in rows:
            kind = _classify(row)
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "ok":
                continue
            details.append({"id": row["id"], "kind": kind,
                            "dest_path": row["dest_path"],
                            "archive_path": row["archive_path"]})

            if not fix:
                continue

            if kind == "stale_active_row":
                _fix_stale_active_row(conn, row)
                fixed_ids.append(row["id"])
            elif kind == "stale_archive_row":
                _fix_stale_archive_row(conn, row)
                fixed_ids.append(row["id"])
            elif kind == "lost":
                _fix_lost(conn, row)
                fixed_ids.append(row["id"])
            elif kind == "both_exist":
                _fix_both_exist(conn, row)
                fixed_ids.append(row["id"])
    finally:
        conn.close()

    if fix and fixed_ids:
        try:
            audit_mod.emit_sync(
                action="PDF_ARCHIVE_REPAIRED", actor_id=actor_id,
                actor_type="user" if actor_id else "system",
                context={"fixed_ids": fixed_ids[:5000],
                         "count": len(fixed_ids), "counts": counts},
            )
        except Exception:
            pass

    return {
        "checked": len(rows),
        "counts": counts,
        "details": details[:200],
        "fixed": len(fixed_ids),
        "applied": fix,
    }
