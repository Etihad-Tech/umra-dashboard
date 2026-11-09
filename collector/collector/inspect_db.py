"""Read-only look at what the collectors stored (no secrets are printed).

    python -m collector.inspect_db            # packages per company + categories that have no tier yet
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import requests


def _get(base: str, key: str, path: str) -> list:
    r = requests.get(f"{base}/rest/v1/{path}", timeout=30,
                     headers={"apikey": key, "Authorization": f"Bearer {key}"})
    r.raise_for_status()
    return r.json()


def main() -> int:
    base, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set", file=sys.stderr)
        return 2
    rows = _get(base, key, "packages?select=company_id,external_key,name,category_raw,tier,nights,departure_city"
                           "&active=eq.true&order=company_id,external_key&limit=1000")
    by_co = defaultdict(list)
    for r in rows:
        by_co[r["company_id"]].append(r)
    for co in sorted(by_co):
        print(f"== {co}: {len(by_co[co])} packages")
        for r in by_co[co]:
            print(f"  {r['external_key']!s:28} cat={r['category_raw']!s:16} tier={r['tier']!s:8} "
                  f"nights={r['nights']} city={r['departure_city']}")
    missing = sorted({(r["company_id"], r["category_raw"]) for r in rows if r["tier"] is None})
    print("\nno tier yet:", missing or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
