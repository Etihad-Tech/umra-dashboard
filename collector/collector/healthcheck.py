"""Is the collector alive?  Exit 0 = every enabled source succeeded recently, 1 = something is stale or unreachable.

    python -m collector.healthcheck

"Recently" = within 3 intervals + 5 minutes of that source's own interval_min. Used as the Docker healthcheck and
safe to call from any monitoring tool. Prints source names only, never keys.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

NAMES = {"B": "ETIHAD", "S": "SAFO", "F": "MANDARIN"}


def stale_sources(rows: list[dict], now: datetime) -> list[str]:
    bad = []
    for r in rows:
        if r.get("enabled") is False:
            continue
        name = NAMES.get(r.get("company_id"), str(r.get("company_id")))
        raw = r.get("last_ok_at")
        try:
            last = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            bad.append(f"{name}: never succeeded")
            continue
        limit = timedelta(minutes=3 * int(r.get("interval_min") or 15) + 5)
        if now - last > limit:
            bad.append(f"{name}: last ok {int((now - last).total_seconds() // 60)} min ago")
    return bad


def main() -> int:
    base, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set", file=sys.stderr)
        return 1
    try:
        r = requests.get(f"{base}/rest/v1/sources?select=company_id,enabled,interval_min,last_ok_at", timeout=15,
                         headers={"apikey": key, "Authorization": f"Bearer {key}"})
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"cannot read sources: {type(e).__name__}", file=sys.stderr)
        return 1
    if not rows:
        print("no sources configured", file=sys.stderr)
        return 1
    bad = stale_sources(rows, datetime.now(timezone.utc))
    for line in bad:
        print(line, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
