r"""CLI wrapper for repair.scan_all — reconciles pdfs archive columns with
disk state on the destination share.

Usage:
  # Dry-run (default) — prints what would change, exits.
  uv run --env-file .env python scripts/repair_archive_state.py

  # Actually apply fixes.
  uv run --env-file .env python scripts/repair_archive_state.py --fix

  # Focus on specific rows.
  uv run --env-file .env python scripts/repair_archive_state.py --only-check 42,43,44 --fix

See repair.py for the algorithm — this file is intentionally thin so
api.py and the CLI both share one implementation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repair import scan_all  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true",
                    help="Apply repairs. Without this flag, dry-run only.")
    ap.add_argument("--only-check", default="",
                    help="Comma-separated row ids to focus on")
    args = ap.parse_args()

    only = [int(x) for x in args.only_check.split(",") if x.strip()] or None
    print(f"[start] mode={'FIX' if args.fix else 'dry-run'}"
          f"{f' only={only}' if only else ''}")

    result = scan_all(fix=args.fix, only_ids=only)

    print(f"[done] checked={result['checked']} fixed={result['fixed']}")
    for kind, n in result["counts"].items():
        if n:
            print(f"  {kind:32s} : {n}")
    if result["details"]:
        print()
        print(f"first {min(len(result['details']), 20)} details:")
        for d in result["details"][:20]:
            print(f"  #{d['id']:>6} {d['kind']}")

    if not args.fix and any(n > 0 for k, n in result["counts"].items() if k != "ok"):
        print()
        print("Dry run — no changes. Re-run with --fix to apply.")


if __name__ == "__main__":
    main()
