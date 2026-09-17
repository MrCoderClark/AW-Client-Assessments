"""Read-only Salesforce schema discovery for the assessment-push integration.

Finds the Assignment object's API name, its Case Number field, and its Account
lookup — the pieces needed to write `Case Number -> Assignment -> Account`.

Run:
  uv run --env-file .env python scripts/sf_discover.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sf import SFClient


def main() -> None:
    sf = SFClient()
    print(f"Connected. API v{sf.version}\n")

    g = sf.describe_global()
    cands = [s for s in g["sobjects"]
             if "assignment" in s["label"].lower() or "assignment" in s["name"].lower()]

    print(f"Objects matching 'assignment' ({len(cands)}):")
    for s in cands:
        print(f"  {s['name']:<40} label={s['label']!r:<30} prefix={s.get('keyPrefix')} "
              f"queryable={s['queryable']}")
    print()

    for s in cands:
        name = s["name"]
        try:
            d = sf.describe(name)
        except Exception as e:
            print(f"[describe-fail] {name}: {e}")
            continue
        fields = d["fields"]
        case_fields = [f for f in fields
                       if "case" in f["label"].lower() or "case" in f["name"].lower()]
        acct_refs = [f for f in fields
                     if f["type"] == "reference" and "Account" in (f.get("referenceTo") or [])]

        print(f"=== {name}  ({len(fields)} fields) ===")
        print("  Case-number candidates:")
        for f in case_fields:
            print(f"    {f['name']:<32} label={f['label']!r:<24} type={f['type']}")
        print("  Account lookup fields:")
        for f in acct_refs:
            print(f"    {f['name']:<32} label={f['label']!r:<24} -> {f['referenceTo']}")
        print()


if __name__ == "__main__":
    main()
