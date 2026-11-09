"""Import the services table data (hotels, airline, meals, train, transfer per package) into Supabase.

    python3 -m collector.import_services ../private/services.json --env .env      (run from the collector folder)
    python3 -m collector.import_services ../private/services.json --dry-run       (only check the file)

The file is NOT in git (private/ is gitignored) and is NOT served by the website: it goes into the
app_settings row 'package_services', which the database shows only to signed-in, active users (RLS).
The dashboard (live.js) reads it after login. Format:

    {"B": {"<our package id>": ENTRY}, "S": {"<Safo package name>": ENTRY}, "F": {"<Mandarin package name>": ENTRY}}
    ENTRY = {"air": "...", "hot": [["Makka", "Hotel", nights, meals 0-3], ...], "train": 0|1, "xfer": "...",
             "meal": 0-3 | null, "exc": "...", "lead": "..."}          (every field optional)

Only standard library: runs on the server's plain python3 like the preflight. Prints counts, never the key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from collector.preflight import parse_env

SETTINGS_KEY = "package_services"
COMPANIES = ("B", "S", "F")
CITIES = ("Makka", "Madina", "Jidda")
TEXT_FIELDS = ("air", "xfer", "exc", "lead")
MAX_BYTES = 256 * 1024
MAX_ENTRIES = 1000
MAX_TEXT = 300


def _text(v, where: str, errors: list) -> None:
    if not isinstance(v, str) or len(v) > MAX_TEXT:
        errors.append(f"{where}: must be text of at most {MAX_TEXT} characters")


def _small_int(v, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def validate(data) -> list:
    """Problems in the services data (empty list = ok)."""
    errors: list = []
    if not isinstance(data, dict) or not data:
        return ["the file must be a JSON object like {\"B\": {...}, \"S\": {...}, \"F\": {...}}"]
    extra = sorted(set(data) - set(COMPANIES))
    if extra:
        errors.append(f"unknown company keys {extra} (only B, S, F)")
    total = 0
    for co in COMPANIES:
        block = data.get(co, {})
        if not isinstance(block, dict):
            errors.append(f"{co}: must be an object of package -> entry")
            continue
        for name, e in block.items():
            total += 1
            where = f"{co}/{name[:40]}"
            if not name.strip() or len(name) > 200:
                errors.append(f"{where}: package name must be 1-200 characters")
            if not isinstance(e, dict):
                errors.append(f"{where}: entry must be an object")
                continue
            unknown = sorted(set(e) - set(TEXT_FIELDS) - {"hot", "train", "meal"})
            if unknown:
                errors.append(f"{where}: unknown fields {unknown}")
            for f in TEXT_FIELDS:
                if f in e:
                    _text(e[f], f"{where}.{f}", errors)
            if "train" in e and not (isinstance(e["train"], bool) or _small_int(e["train"], 0, 1)):
                errors.append(f"{where}.train: must be 0 or 1")
            if "meal" in e and e["meal"] is not None and not _small_int(e["meal"], 0, 3):
                errors.append(f"{where}.meal: must be 0-3 or null")
            hot = e.get("hot", [])
            if not isinstance(hot, list):
                errors.append(f"{where}.hot: must be a list")
                continue
            for i, h in enumerate(hot):
                ok = (isinstance(h, list) and len(h) == 4 and h[0] in CITIES and isinstance(h[1], str)
                      and 0 < len(h[1]) <= MAX_TEXT and _small_int(h[2], 0, 60) and _small_int(h[3], 0, 3))
                if not ok:
                    errors.append(f"{where}.hot[{i}]: must be [city ({'/'.join(CITIES)}), hotel, nights 0-60, meals 0-3]")
    if total > MAX_ENTRIES:
        errors.append(f"too many entries ({total} > {MAX_ENTRIES})")
    return errors


def counts(data: dict) -> str:
    return ", ".join(f"{co} {len(data.get(co) or {})}" for co in COMPANIES)


def upload(base: str, key: str, data: dict, timeout: float = 30.0) -> None:
    body = json.dumps([{"key": SETTINGS_KEY, "value": data,
                        "updated_at": datetime.now(timezone.utc).isoformat()}]).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/app_settings?on_conflict=key", data=body, method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (URL is checked in main)
        if r.status not in (200, 201, 204):
            raise RuntimeError(f"unexpected HTTP {r.status}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import services table data into Supabase app_settings.")
    ap.add_argument("file", help="services JSON (keep it out of git, e.g. private/services.json)")
    ap.add_argument("--env", help="env file with SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (default: environment)")
    ap.add_argument("--dry-run", action="store_true", help="only check the file")
    a = ap.parse_args(argv)

    try:
        raw = open(a.file, "rb").read()
    except OSError as e:
        print(f"cannot read {e.filename}", file=sys.stderr)
        return 2
    if len(raw) > MAX_BYTES:
        print(f"file is larger than {MAX_BYTES} bytes", file=sys.stderr)
        return 1
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        print(f"not valid JSON: {type(e).__name__}", file=sys.stderr)
        return 1
    problems = validate(data)
    for p in problems[:50]:
        print("PROBLEM:", p, file=sys.stderr)
    if problems:
        return 1
    data = {co: data.get(co) or {} for co in COMPANIES}
    if a.dry_run:
        print(f"import_services: file ok ({counts(data)} entries), nothing uploaded (--dry-run)")
        return 0

    env = dict(os.environ)
    if a.env:
        try:
            env.update(parse_env(open(a.env, encoding="utf-8").read()))
        except OSError as e:
            print(f"cannot read {e.filename}", file=sys.stderr)
            return 2
    base, key = env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not re.fullmatch(r"https://[a-z0-9]+\.supabase\.co/?", base) or not key:
        print("SUPABASE_URL (https://<project>.supabase.co) and SUPABASE_SERVICE_ROLE_KEY are needed", file=sys.stderr)
        return 2
    try:
        upload(base, key, data)
    except urllib.error.HTTPError as e:
        print(f"upload failed: HTTP {e.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, RuntimeError) as e:
        print(f"upload failed: {type(e).__name__}", file=sys.stderr)
        return 1
    print(f"import_services: ok, app_settings '{SETTINGS_KEY}' now has {counts(data)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
