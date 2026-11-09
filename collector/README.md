# Collectors (Phase 3) and Telegram alerts (Phase 4)

Pull competitor prices and store them in Supabase. The ETIHAD collector comes next.
Competitor addresses are not in this public repo: set `MANDARIN_URL` and `SAFO_SITE_URL` in the environment.

## Sources
- **Mandarin** (`--source mandarin`): JSON feed. Departure city = `sources.config.departure_city` (default `TOSHKENT`).
- **Safo** (`--source safo`): the public site loads packages from its own Supabase REST API with a public (anon) key
  embedded in the page. The collector reads that key from the page at run time (never stored; a key with any role
  other than `anon` is refused) and makes the same single request as the page:
  `landing_packages?select=*&is_active=eq.true`, plus `order=id.asc` and `limit`/`offset` pages (read until an
  empty page, so a server row cap cannot cut the list). No 1-person price (`pax=1` is never written).
  A date without a year (`17.09`) is the one that passed at most 45 days ago (a trip that just left and is still
  listed keeps its year, also across New Year), otherwise the next occurrence. A return date without a year is the
  next one on or after the departure. "Today" is always the **Asia/Tashkent** date, not the server's UTC date.
  Requests per run: the page, up to 15 of the site's own scripts (the key may live there), then the data pages
  (normally one + one empty page). A failed attempt repeats all of it (3 attempts).
  Assumption to confirm with `--dry-run`: package = `section`, departure = one row (`date_out`).

## What it does
- `GET` the feed, keep one departure city (default `TOSHKENT`, set in `sources.config.departure_city`).
- Packages = `family` + departure city; departures = package + `date_key`; prices = 4/3/2/1 pax.
- A `price_snapshots` row is written **only when a price changed**. No price = `NULL`, never `0`.
  All four pax are always compared: a pax that is no longer listed becomes `NULL` (not "unchanged").
- Prices: `1,190 $`, `1 190`, `1.190`, `1 190,00 $` -> 1190. Text with two numbers (`2 x 1190`), or an amount below
  $100 / above $100,000, is not a price (NULL). Not a single price in the whole feed = failed run (format changed).
- Categories go through the `category_map` table (`LUXE` -> `lux`, ...). Unknown -> `tier` NULL (see view `unmapped_categories`).
- 0 packets, a broken feed or a network error = **failed run** (`collector_runs.status = 'failed'`, `sources.consecutive_failures` +1).
  One broken ETIHAD package (e.g. `nights` 0 / negative / `true`) is skipped and counted as invalid, the rest is synced.
- A departure missing from the feed for 2 runs in a row becomes `active = false`. A departure whose date has come
  (Tashkent date) is deactivated at once and silently: trips that left are not news.
- A package or date that an editor added by hand and that the source now publishes is **taken over** by the source:
  `is_manual` is cleared, the source's values win, and one `manual_conflict` event (with `"takeover"` in old/new)
  tells the editors what changed. Afterwards it is an ordinary automatic row (normal `removed` logic). Price overrides
  (`manual_entries`) keep working as before.

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
pip install --require-hashes -r requirements.txt pytest   # requirements.txt pins exact versions + sha256 hashes
python -m pytest -q                                       # or: python -m unittest discover -s tests -t . -v
```
Tests use synthetic fixtures in `tests/fixtures/` (feed formats from the TASK) and an in-memory store.

## Events and Telegram (Phase 4)
Every run records what changed in `events`: `price_down` / `price_up`, `new_package` (once, not per date),
`new_departure`, `sold_out` / `back_on_sale`, `removed` (a date missing for 2 runs in a row),
`source_down` (exactly once, at the 3rd failed run in a row) and `source_recovered`.
- The **first successful run of a source is a seed**: it fills the database and creates no events.
- A price that appears or disappears (NULL <-> number) is stored in the history but is not announced as up/down.
  Up/down compares with the last KNOWN price, so `$1,000 -> no price -> $900` is still announced as a drop.

`python -m collector.notify` sends unsent events to one private Telegram chat, in Uzbek:
`🔻 MANDARIN · Lux · HYATT-09 · 12.12 — 4 kishi: $2,290 → $2,140 (−$150)`, plus ` · ETIHAD <name>: $1,990`
when the competitor package is matched to one of ours (`package_matches`, same date and pax).
- One message per company per pass (split only if it would exceed Telegram's limit). Sent events get `telegram_sent_at`,
  so a restart never resends. Telegram 429 -> waits `retry_after` (up to 60 s; longer = the events wait for the next
  cycle); any other failure leaves the events for the next pass.
- Setup: create a NEW bot with @BotFather (not the pilgrim or support bots), put the token in `.env`
  as `TELEGRAM_BOT_TOKEN`, add the bot to a private group, then store the chat id in the database
  (SQL Editor): `insert into app_settings (key, value) values ('telegram', '{"chat_id": "-100...", "enabled": true}')`.
  `"enabled": false` mutes it. Without the row, `TELEGRAM_CHAT_ID` from `.env` is used.
- The token is only read from the environment and is never logged (error texts never contain the request URL).
- Run exactly ONE notifier process.
```bash
python -m collector.notify --dry-run     # print the messages, send nothing, mark nothing
python -m collector.notify               # one pass
python -m collector.run --source all --loop --notify   # collect + notify
```

## Server mode (`--loop`)
- Each source and the Telegram step have their own guard: a database hiccup while starting one source skips only it.
- Watchdog: if no cycle has finished for longer than max(3 x interval, 20 min), the process exits with code 70 and
  Docker's restart policy starts a fresh one (Docker itself never restarts an "unhealthy" container).
- `python -m collector.healthcheck` (the Docker healthcheck) checks every enabled source except `kind = 'manual'`.
