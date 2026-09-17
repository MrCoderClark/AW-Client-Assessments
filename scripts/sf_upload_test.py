"""Push a PDF to a client's Salesforce Files via case number, with dedupe.

WRITE test — run only against a sandbox test record (default: Goku TEST).
Resolves the case number to the Person Account, skips if a file with the same
title already exists, otherwise uploads a ContentVersion and re-lists to confirm.

Run (generates a tiny test PDF if --file is omitted):
  uv run --env-file .env python scripts/sf_upload_test.py 9876543210
  uv run --env-file .env python scripts/sf_upload_test.py 9876543210 --file some.pdf --title "VIA_..."
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sf import SFClient


def _test_pdf(text: str) -> bytes:
    """Minimal valid PDF (blank page) via pypdf — already a project dependency."""
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_number", nargs="?", default="9876543210")
    ap.add_argument("--file", help="local PDF to upload (default: a generated test PDF)")
    ap.add_argument("--title", help="ContentVersion title (default: derived)")
    args = ap.parse_args()

    sf = SFClient()
    print(f"Connected (API v{sf.version}).")

    # 1. resolve
    acct = sf.account_for_case_number(args.case_number)
    if not acct:
        print(f"✗ No account for case number {args.case_number!r}.")
        return
    if acct["multi_account"]:
        print(f"⚠ case number maps to multiple accounts — aborting.")
        return
    print(f"→ Client: {acct['name']}  ({acct['account_id']})  "
          f"personAccount={acct['is_person_account']}  assignments={acct['assignments']}")

    # 2. payload
    if args.file:
        data = Path(args.file).read_bytes()
        fname = Path(args.file).name
    else:
        data = _test_pdf("test")
        fname = "VIA_Character_Strengths_Profile-Goku_Test.pdf"
    title = args.title or Path(fname).stem
    print(f"  file: {fname}  ({len(data)} bytes)   title: {title!r}")

    # 3. dedupe by title
    existing = sf.list_account_files(acct["account_id"])
    print(f"  existing files on account: {len(existing)}")
    if any((f["title"] or "").lower() == title.lower() for f in existing):
        print(f"\n⏭  A file titled {title!r} is already on this account — skipping (dedupe).")
        return

    # 4. upload
    print("→ Uploading ContentVersion…")
    cv_id = sf.upload_file(acct["account_id"], title, fname, data)
    print(f"✓ ContentVersion created: {cv_id}")

    # 5. confirm
    after = sf.list_account_files(acct["account_id"])
    print(f"\nFiles on account now: {len(after)}")
    for f in after:
        kb = round((f["size"] or 0) / 1024, 1)
        print(f"  • {f['title']}.{f['ext']}  ({kb} KB)")
    print("\n✅ Upload path works end-to-end.")


if __name__ == "__main__":
    main()
