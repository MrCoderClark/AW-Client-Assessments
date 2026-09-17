"""Scan exactly one PC by name. Prints every SMB step with `[scan]` prefix.

Usage:
  uv run --env-file .env python scripts/scan_one_pc.py --pc PC6

Handy for debugging hangs — the scanner is sequential across 24 PCs and one
bad file (locked, huge, on a slow share) will stall the whole batch. Run this
against the suspect PC to see exactly which file kills the read.

Ctrl+C to abort. The DB row for pc_status updates the same way a full scan does.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect
from pcs import PCS
from scan import _creds, _scan_pc, current_week_range


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pc", required=True, help=f"PC name, one of: {', '.join(PCS.keys())}")
    args = ap.parse_args()

    if args.pc not in PCS:
        sys.exit(f"unknown PC {args.pc!r} — valid: {', '.join(PCS.keys())}")

    host = PCS[args.pc]
    user, password = _creds()
    week_start, week_end = current_week_range()
    conn = connect()

    print(f"[scan] target {args.pc} ({host})")
    print(f"[scan] window {week_start:%Y-%m-%d} → {week_end:%Y-%m-%d}")

    try:
        for line in _scan_pc(args.pc, host, conn, week_start, week_end, user, password):
            print(line, flush=True)
    except Exception as e:
        print(f"[scan] FAILED: {e.__class__.__name__}: {e}", flush=True)
        raise


if __name__ == "__main__":
    main()
