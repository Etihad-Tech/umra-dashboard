"""Entry point:  python -m collector.run [--source mandarin|safo|etihad|all] [--dry-run] [--loop]

--dry-run   fetch + parse and print a summary. No database, no Supabase key needed.
--loop      repeat forever every sources.interval_min minutes (a server may use cron instead).

Competitor addresses are NOT kept in this public repository; set them in the environment:
    MANDARIN_URL   full URL of the Mandarin JSON feed
    SAFO_SITE_URL  address of the Safo public site
    ETIHAD_API_URL / ETIHAD_API_KEY  our partner API (the key is sent in the X-Api-Key header, never in the URL)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from collections import Counter
from datetime import date

import requests

from . import etihad, notify, safo
from .clock import today_tashkent
from .db import RestStore
from .mandarin import SchemaError, parse_feed
from .sync import sync_packets

log = logging.getLogger("collector")

MAX_BYTES = 20 * 1024 * 1024
KINDS = {"mandarin": "mandarin_json", "safo": "safo_supabase", "etihad": "etihad_showroom"}


# ------------------------------------------------------------------ fetching
def fetch_feed(url: str, session: requests.Session | None = None, retries: int = 2, headers: dict | None = None):
    s = session or requests.Session()
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = s.get(url, timeout=30, headers={"User-Agent": "umra-price-monitor/1.0", **(headers or {})})
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


def _execute(store, kind: str, produce, today: date | None = None) -> None:
    """produce(source) -> (ParseResult, city). Any failure marks the run failed, never a silent success.
    `today` (Tashkent date) is only passed by tests."""
    source = store.get_source(kind)
    if not source.get("enabled", True):
        log.info("%s: source is disabled, skipping", kind)
        return
    seeded = store.has_ok_run(source["id"])   # the very first successful run only fills the database (no events)
    was_down = (source.get("consecutive_failures") or 0) >= FAIL_THRESHOLD
    run_id = store.start_run(source["id"])
    try:
        parsed, city = produce(source)
        stats = sync_packets(store, source, run_id, parsed.packets, city, emit_events=seeded, today=today)
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
    log.info("%s: ok, %s items, %s new packages, %s new departures, %s price changes, %s deactivated, "
             "%s hand-added rows taken over (skipped: %s invalid, %s other city, %s duplicates)", kind, stats.items_seen,
             stats.new_packages, stats.new_departures, stats.snapshots, stats.deactivated, stats.taken_over,
             parsed.invalid, parsed.other_city, parsed.duplicates)


def run_once(store, url: str, city_override: str | None = None, today: date | None = None) -> None:
    """Mandarin."""
    def produce(source):
        city = (city_override or (source.get("config") or {}).get("departure_city") or "TOSHKENT").upper()
        return parse_feed(fetch_feed(url), city), city
    _execute(store, KINDS["mandarin"], produce, today)


def run_safo(store, site_url: str, today: date | None = None) -> None:
    day = today or today_tashkent()          # one date for the parse (yearless dates) and the sync (left trips)

    def produce(_source):
        return safo.parse_rows(fetch_safo_rows(site_url), day), None
    _execute(store, KINDS["safo"], produce, day)


def run_etihad(store, url: str, api_key: str, today: date | None = None) -> None:
    def produce(_source):
        return etihad.parse_packages(fetch_feed(url, headers={"X-Api-Key": api_key})), None
    _execute(store, KINDS["etihad"], produce, today)


def run_cycle(store, names: list[str], urls: dict, api_key: str | None = None, city: str | None = None,
              do_notify: bool = False) -> None:
    """One pass over the sources, then Telegram. Each piece has its own guard: a database hiccup while
    starting Mandarin must not skip Safo, ETIHAD or the alerts. Only the error TYPE is logged (messages
    can contain URLs)."""
    runners = {"mandarin": lambda: run_once(store, urls["mandarin"], city),
               "safo": lambda: run_safo(store, urls["safo"]),
               "etihad": lambda: run_etihad(store, urls["etihad"], api_key)}
    for n in names:
        try:
            runners[n]()
        except Exception as e:  # noqa: BLE001
            log.error("%s: run aborted: %s", n, type(e).__name__)
    if do_notify:
        try:
            notify.run_from_env(store)
        except Exception as e:  # noqa: BLE001
            log.error("notify: pass aborted: %s", type(e).__name__)


# ------------------------------------------------------------------ dry runs
def _summary(parsed) -> None:
    print(f"items: {parsed.total} | usable: {len(parsed.packets)} | invalid: {parsed.invalid} | "
          f"other city: {parsed.other_city} | duplicates: {parsed.duplicates}")
    print("categories:", dict(Counter(p.category_raw or "(empty)" for p in parsed.packets)))
    print("packets with no price at all:",
          sum(1 for p in parsed.packets if not any(v is not None for v in p.prices.values())))
    for p in parsed.packets[:3]:
        print(" sample:", p.family, p.date, p.return_date, p.category_raw, p.prices, p.sold_out)


def dry_run_mandarin(url: str, city: str) -> None:
    _summary(parse_feed(fetch_feed(url), city.upper()))


def dry_run_safo(site_url: str) -> None:
    rows = fetch_safo_rows(site_url)
    if rows:
        print("columns in first row:", sorted(rows[0].keys()))
    _summary(safo.parse_rows(rows, today_tashkent()))


def dry_run_etihad(url: str, api_key: str) -> None:
    _summary(etihad.parse_packages(fetch_feed(url, headers={"X-Api-Key": api_key})))


def _need_env(name: str) -> str | None:
    value = os.environ.get(name)
    if not value:
        print(f"{name} is not set (see .env.example)", file=sys.stderr)
    return value


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["mandarin", "safo", "etihad", "all"], default="mandarin")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--notify", action="store_true", help="after each run, send new events to Telegram")
    ap.add_argument("--city", help="Mandarin only: override departure city (default: sources.config, else TOSHKENT)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    names = ["mandarin", "safo", "etihad"] if args.source == "all" else [args.source]

    urls = {}
    for n in names:
        urls[n] = _need_env({"mandarin": "MANDARIN_URL", "safo": "SAFO_SITE_URL", "etihad": "ETIHAD_API_URL"}[n])
        if not urls[n]:
            return 2

    api_key = None
    if "etihad" in names:
        api_key = _need_env("ETIHAD_API_KEY")
        if not api_key:
            return 2

    if args.dry_run:
        for n in names:
            print(f"== {n}")
            try:
                if n == "mandarin":
                    dry_run_mandarin(urls[n], args.city or "TOSHKENT")
                elif n == "safo":
                    dry_run_safo(urls[n])
                else:
                    dry_run_etihad(urls[n], api_key)
            except Exception as e:  # noqa: BLE001
                print(f"FAILED: {type(e).__name__}: {e}")
                return 1
        return 0

    base, key = _need_env("SUPABASE_URL"), _need_env("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        return 2
    store = RestStore(base, key)

    def cycle() -> None:
        run_cycle(store, names, urls, api_key, args.city, args.notify)

    if not args.loop:
        cycle()
        return 0
    watchdog = Watchdog()
    watchdog.start()
    return loop_forever(cycle, lambda: min(store.get_source(KINDS[n]).get("interval_min", 15) for n in names) * 60,
                        watchdog=watchdog)


class Watchdog:
    """Restarts a STUCK collector: if the main loop has not finished a cycle for longer than
    max(3 x interval, 20 min), the process exits (code 70) and Docker's restart policy starts a fresh one.
    A hang (e.g. a network read that never ends) is otherwise invisible: the alerts run in the same loop."""
    EXIT_CODE = 70

    def __init__(self, min_limit: float = 20 * 60, interval: float = 15 * 60, check_every: float = 60,
                 clock=time.monotonic, exit_process=os._exit, sleep=time.sleep):
        self.min_limit, self.check_every = min_limit, check_every
        self.clock, self.exit_process, self.sleep = clock, exit_process, sleep
        self.last = clock()
        self.limit = max(3 * interval, min_limit)

    def beat(self, interval: float) -> None:
        """Called after every finished cycle with the wait before the next one."""
        self.limit = max(3 * interval, self.min_limit)
        self.last = self.clock()

    def check(self) -> bool:
        idle = self.clock() - self.last
        if idle > self.limit:
            log.critical("watchdog: no finished cycle for %d min (limit %d min), exiting so the container restarts",
                         idle // 60, self.limit // 60)
            self.exit_process(self.EXIT_CODE)
            return False
        return True

    def _run(self) -> None:
        while self.check():         # check() never returns False for real: os._exit ends the process
            self.sleep(self.check_every)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._run, name="watchdog", daemon=True)
        t.start()
        return t


def loop_forever(cycle, interval_seconds, sleep=time.sleep, watchdog: Watchdog | None = None) -> int:
    """Server mode: one broken cycle (database or network down) must never stop the collector."""
    while True:
        try:
            cycle()
        except Exception as e:  # noqa: BLE001  - log the type only, messages may contain URLs
            log.error("cycle failed: %s", type(e).__name__)
        try:
            wait = interval_seconds()
        except Exception:  # noqa: BLE001
            wait = 15 * 60
        wait = max(60, min(wait, 24 * 3600))
        if watchdog is not None:
            watchdog.beat(wait)
        sleep(wait)

if __name__ == "__main__":
    sys.exit(main())
