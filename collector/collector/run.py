"""Entry point:  python -m collector.run [--source mandarin|safo|all] [--dry-run] [--loop]

--dry-run   fetch + parse and print a summary. No database, no Supabase key needed.
--loop      repeat forever every sources.interval_min minutes (a server may use cron instead).

Competitor addresses are NOT kept in this public repository; set them in the environment:
    MANDARIN_URL   full URL of the Mandarin JSON feed
    SAFO_SITE_URL  address of the Safo public site
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import Counter
from datetime import date

import requests

from . import notify, safo
from .db import RestStore
from .mandarin import SchemaError, parse_feed
from .sync import sync_packets

log = logging.getLogger("collector")

MAX_BYTES = 20 * 1024 * 1024
KINDS = {"mandarin": "mandarin_json", "safo": "safo_supabase"}


# ------------------------------------------------------------------ fetching
def fetch_feed(url: str, session: requests.Session | None = None, retries: int = 2):
    s = session or requests.Session()
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = s.get(url, timeout=30, headers={"User-Agent": "umra-price-monitor/1.0"})
            r.raise_for_status()
            if len(r.content) > MAX_BYTES:
                raise SchemaError("feed is unexpectedly large")
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"cannot fetch feed: {type(last).__name__}")


def fetch_safo_rows(site_url: str) -> list:
    last: Exception | None = None
    for attempt in range(3):
        try:
            base, key = safo.discover(site_url)
            return safo.fetch_rows(base, key)
        except SchemaError:
            raise
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"cannot fetch safo data: {type(last).__name__}")


# --------------------------------------------------------------------- runs
FAIL_THRESHOLD = 3   # consecutive failed runs before a "source_down" alert (one flaky run is not an outage)


def _source_event(kind: str, source: dict, payload: dict) -> dict:
    return {"type": kind, "company_id": source["company_id"], "package_id": None, "departure_id": None,
            "old": None, "new": payload, "is_manual": False}


def _execute(store, kind: str, produce) -> None:
    """produce(source) -> (ParseResult, city). Any failure marks the run failed, never a silent success."""
    source = store.get_source(kind)
    if not source.get("enabled", True):
        log.info("%s: source is disabled, skipping", kind)
        return
    seeded = store.has_ok_run(source["id"])   # the very first successful run only fills the database (no events)
    was_down = (source.get("consecutive_failures") or 0) >= FAIL_THRESHOLD
    run_id = store.start_run(source["id"])
    try:
        parsed, city = produce(source)
        stats = sync_packets(store, source, run_id, parsed.packets, city, emit_events=seeded)
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        log.error("%s: run failed: %s", kind, msg)
        store.finish_run(run_id, "failed", error=msg)
        failures = store.mark_source_failed(source, msg)
        if failures == FAIL_THRESHOLD:        # exactly once per outage, not on every further failure
            store.insert_events([_source_event("source_down", source, {"failures": failures, "error": msg[:200]})])
        return
    store.finish_run(run_id, "ok", items_seen=stats.items_seen, changes=stats.changes)
    store.mark_source_ok(source)
    if was_down:
        store.insert_events([_source_event("source_recovered", source, {})])
    log.info("%s: ok, %s items, %s new packages, %s new departures, %s price changes, %s deactivated "
             "(skipped: %s invalid, %s other city, %s duplicates)", kind, stats.items_seen,
             stats.new_packages, stats.new_departures, stats.snapshots, stats.deactivated,
             parsed.invalid, parsed.other_city, parsed.duplicates)


def run_once(store, url: str, city_override: str | None = None) -> None:
    """Mandarin."""
    def produce(source):
        city = (city_override or source.get("config", {}).get("departure_city") or "TOSHKENT").upper()
        return parse_feed(fetch_feed(url), city), city
    _execute(store, KINDS["mandarin"], produce)


def run_safo(store, site_url: str, today: date | None = None) -> None:
    def produce(_source):
        return safo.parse_rows(fetch_safo_rows(site_url), today or date.today()), None
    _execute(store, KINDS["safo"], produce)


# ------------------------------------------------------------------ dry runs
def _summary(parsed) -> None:
    print(f"items: {parsed.total} | usable: {len(parsed.packets)} | invalid: {parsed.invalid} | "
          f"other city: {parsed.other_city} | duplicates: {parsed.duplicates}")
    print("categories:", dict(Counter(p.category_raw or "(empty)" for p in parsed.packets)))
    print("packets with no price at all:",
          sum(1 for p in parsed.packets if not any(p.prices.values())))
    for p in parsed.packets[:3]:
        print(" sample:", p.family, p.date, p.return_date, p.category_raw, p.prices, p.sold_out)


def dry_run_mandarin(url: str, city: str) -> None:
    _summary(parse_feed(fetch_feed(url), city.upper()))


def dry_run_safo(site_url: str) -> None:
    rows = fetch_safo_rows(site_url)
    if rows:
        print("columns in first row:", sorted(rows[0].keys()))
    _summary(safo.parse_rows(rows, date.today()))


def _need_env(name: str) -> str | None:
    value = os.environ.get(name)
    if not value:
        print(f"{name} is not set (see .env.example)", file=sys.stderr)
    return value


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["mandarin", "safo", "all"], default="mandarin")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--notify", action="store_true", help="after each run, send new events to Telegram")
    ap.add_argument("--city", help="Mandarin only: override departure city (default: sources.config, else TOSHKENT)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    names = ["mandarin", "safo"] if args.source == "all" else [args.source]

    urls = {}
    for n in names:
        urls[n] = _need_env("MANDARIN_URL" if n == "mandarin" else "SAFO_SITE_URL")
        if not urls[n]:
            return 2

    if args.dry_run:
        for n in names:
            print(f"== {n}")
            try:
                dry_run_mandarin(urls[n], args.city or "TOSHKENT") if n == "mandarin" else dry_run_safo(urls[n])
            except Exception as e:  # noqa: BLE001
                print(f"FAILED: {type(e).__name__}: {e}")
                return 1
        return 0

    base, key = _need_env("SUPABASE_URL"), _need_env("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        return 2
    store = RestStore(base, key)
    while True:
        for n in names:
            run_once(store, urls[n], args.city) if n == "mandarin" else run_safo(store, urls[n])
        if args.notify:
            notify.run_from_env(store)
        if not args.loop:
            return 0
        time.sleep(min(store.get_source(KINDS[n]).get("interval_min", 15) for n in names) * 60)


if __name__ == "__main__":
    sys.exit(main())
