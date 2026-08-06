"""Populate pdfs.text_hash for existing rows, then report content-duplicate
clusters (rows with same text_hash pointing at different files on disk).

For committed rows we read dest_path from the network share.
For uncommitted rows we read source_path from the origin PC.

Skips silently if a file is missing / unreachable — those get picked up
on the next scan.

Run:  uv run --env-file .env python scripts/backfill_text_hash.py
Options:
  --limit N     stop after N rows (useful for dry testing)
  --dry-run     compute + report, don't write
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smbclient
from pypdf import PdfReader

from db import connect
from scan import text_hash


def _register_smb(host: str) -> None:
    dest_share = os.environ.get("DEST_SHARE", r"\\192.168.70.10\Client_Assessments")
    dest_host = dest_share.lstrip("\\").split("\\", 1)[0]
    if host == dest_host:
        u, p = os.environ.get("DEST_SMB_USER"), os.environ.get("DEST_SMB_PASS")
        timeout = 10
    else:
        u, p = os.environ.get("SMB_USER"), os.environ.get("SMB_PASS")
        timeout = 5
    if not (u and p):
        raise RuntimeError(f"SMB creds missing for host {host}")
    smbclient.register_session(host, username=u, password=p, connection_timeout=timeout)


def _read_and_hash(path: str) -> str | None:
    host = path.lstrip("\\").split("\\", 1)[0]
    _register_smb(host)
    with smbclient.open_file(path, mode="rb") as f:
        data = f.read()
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:3])
    except Exception:
        return None
    return text_hash(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = connect()
    sql = """
        SELECT id, host, source_path, dest_path, committed_at, filename, proposed_name
        FROM pdfs
        WHERE text_hash IS NULL
        ORDER BY committed_at NULLS LAST, id
    """
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql).fetchall()
    print(f"[start] {len(rows)} rows need text_hash")

    ok = missing = fail = 0
    for r in rows:
        path = r["dest_path"] if r["committed_at"] else r["source_path"]
        if not path:
            missing += 1
            continue
        try:
            h = _read_and_hash(path)
        except FileNotFoundError:
            missing += 1
            print(f"  [gone] id={r['id']} {path}")
            continue
        except OSError as e:
            if "not found" in str(e).lower() or "cannot find" in str(e).lower():
                missing += 1
                print(f"  [gone] id={r['id']} {path}")
                continue
            fail += 1
            print(f"  [fail] id={r['id']} {e.__class__.__name__}: {e}")
            continue
        except Exception as e:
            fail += 1
            print(f"  [fail] id={r['id']} {e.__class__.__name__}: {e}")
            continue

        if h is None:
            # Couldn't extract text — probably an image-only PDF. Leave NULL.
            missing += 1
            continue

        if not args.dry_run:
            conn.execute("UPDATE pdfs SET text_hash = %s WHERE id = %s", (h, r["id"]))
        ok += 1
    if not args.dry_run:
        conn.commit()

    print(f"[done] hashed={ok}  missing/no-text={missing}  fail={fail}")
    print()

    # Report same-text_hash clusters (content duplicates already on disk).
    print("[dupes] content-duplicate clusters in committed files:")
    dup_rows = conn.execute("""
        SELECT text_hash, COUNT(*) AS n
        FROM pdfs
        WHERE text_hash IS NOT NULL AND committed_at IS NOT NULL
        GROUP BY text_hash
        HAVING COUNT(*) > 1
        ORDER BY n DESC
    """).fetchall()
    if not dup_rows:
        print("  (none)")
        return
    for cluster in dup_rows:
        h = cluster["text_hash"]
        n = cluster["n"]
        print(f"\n  cluster ({n} rows) text_hash={h[:16]}…")
        for r in conn.execute("""
            SELECT id, host, dest_path, committed_at
            FROM pdfs
            WHERE text_hash = %s AND committed_at IS NOT NULL
            ORDER BY committed_at
        """, (h,)).fetchall():
            print(f"    id={r['id']:>4}  committed={str(r['committed_at'])[:19]}  {r['dest_path']}")


if __name__ == "__main__":
    main()
