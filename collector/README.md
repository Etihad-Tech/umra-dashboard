# Collectors (Phase 3): Mandarin and Safo

Pull competitor prices and store them in Supabase. The ETIHAD collector comes next.
Competitor addresses are not in this public repo: set `MANDARIN_URL` and `SAFO_SITE_URL` in the environment.

## Sources
- **Mandarin** (`--source mandarin`): JSON feed. Departure city = `sources.config.departure_city` (default `TOSHKENT`).
- **Safo** (`--source safo`): the public site loads packages from its own Supabase REST API with a public (anon) key
  embedded in the page. The collector reads that key from the page at run time (never stored; a key with any role
  other than `anon` is refused) and makes the same single request as the page:
  `landing_packages?select=*&is_active=eq.true`. No 1-person price (`pax=1` is never written).
  A date without a year (`17.09`) is the next occurrence on or after today.
  Assumption to confirm with `--dry-run`: package = `section`, departure = one row (`date_out`).

## What it does
- `GET` the feed, keep one departure city (default `TOSHKENT`, set in `sources.config.departure_city`).
- Packages = `family` + departure city; departures = package + `date_key`; prices = 4/3/2/1 pax.
- A `price_snapshots` row is written **only when a price changed**. No price = `NULL`, never `0`.
- Categories go through the `category_map` table (`LUXE` -> `lux`, ...). Unknown -> `tier` NULL (see view `unmapped_categories`).
- 0 packets, a broken feed or a network error = **failed run** (`collector_runs.status = 'failed'`, `sources.consecutive_failures` +1).
- A departure missing from the feed for 2 runs in a row becomes `active = false`.

## Check the feed first (no database, no key)
```bash
cd collector
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export MANDARIN_URL=...   # and/or SAFO_SITE_URL=...
python -m collector.run --source all --dry-run
```

## Real run
Needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in the environment (see `.env.example`).
The service_role key bypasses RLS: keep it only on the server, never in git, chats or the browser.
```bash
set -a; source .env; set +a
python -m collector.run --source all           # one run of both sources
python -m collector.run --source all --loop    # repeat every sources.interval_min minutes
```

## Tests
```bash
python -m unittest discover -s tests -t . -v
```
Tests use synthetic fixtures in `tests/fixtures/` (feed formats from the TASK) and an in-memory store.
