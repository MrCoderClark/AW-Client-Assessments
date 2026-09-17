"""Read-only: resolve a Case Number -> client Account -> existing files.

Proves the lookup path for the assessment-push integration:
  Assignment__c.Name (= Case Number) -> Participant__c (Person Account) -> Files

Run:
  uv run --env-file .env python scripts/sf_resolve.py 00039658168I
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sf import SFClient


def soql_str(v: str) -> str:
    """Escape a value for a single-quoted SOQL literal."""
    return v.replace("\\", "\\\\").replace("'", "\\'")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_number", nargs="?", default="00039658168I")
    args = ap.parse_args()
    cn = args.case_number.strip()

    sf = SFClient()
    print(f"Connected (API v{sf.version}). Resolving case number: {cn}\n")

    # 1. Case Number -> Assignment(s) -> Participant (Person Account)
    esc = soql_str(cn)
    res = sf.soql(
        "SELECT Id, Name, Participant__c, Participant__r.Name, "
        "Participant__r.IsPersonAccount, Participant__r.PersonEmail "
        f"FROM Assignment__c WHERE Name = '{esc}'"
    )
    recs = res.get("records", [])
    if not recs:
        print("✗ No Assignment found with that Case Number.")
        return

    print(f"Assignments with this case number: {len(recs)}")
    for r in recs:
        p = r.get("Participant__r") or {}
        print(f"  - {r['Id']}  participant={p.get('Name')}  personAccount={p.get('IsPersonAccount')}")

    account_ids = {r["Participant__c"] for r in recs if r.get("Participant__c")}
    if len(account_ids) != 1:
        print(f"\n⚠ Expected exactly one client account, got {len(account_ids)}: {account_ids}")
        if not account_ids:
            return
    account_id = next(iter(account_ids))
    first = next(r for r in recs if r.get("Participant__c") == account_id)
    acct = first.get("Participant__r") or {}
    print(f"\n→ Resolved client Account: {acct.get('Name')}  ({account_id})")
    print(f"  person account: {acct.get('IsPersonAccount')}   email: {acct.get('PersonEmail')}\n")

    # 2. List files already attached to that Account (for the upload dedupe check)
    files = sf.soql(
        "SELECT ContentDocumentId, ContentDocument.Title, ContentDocument.FileExtension, "
        "ContentDocument.ContentSize, ContentDocument.CreatedDate "
        f"FROM ContentDocumentLink WHERE LinkedEntityId = '{soql_str(account_id)}'"
    )
    frecs = files.get("records", [])
    print(f"Files currently on this account: {len(frecs)}")
    for f in frecs:
        d = f.get("ContentDocument") or {}
        size_kb = round((d.get("ContentSize") or 0) / 1024, 1)
        print(f"  • {d.get('Title')}.{d.get('FileExtension')}  ({size_kb} KB)  {d.get('CreatedDate','')[:10]}")

    print("\n✅ Resolve path works end-to-end (read-only).")


if __name__ == "__main__":
    main()
