# Collectors (Phase 3) and Telegram alerts (Phase 4)

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

## Events and Telegram (Phase 4)
Every run records what changed in `events`: `price_down` / `price_up`, `new_package` (once, not per date),
`new_departure`, `sold_out` / `back_on_sale`, `removed` (a date missing for 2 runs in a row),
`source_down` (exactly once, at the 3rd failed run in a row) and `source_recovered`.
- The **first successful run of a source is a seed**: it fills the database and creates no events.
- A price that appears or disappears (NULL <-> number) is stored in the history but is not announced as up/down.

`python -m collector.notify` sends unsent events to one private Telegram chat, in Uzbek:
`🔻 MANDARIN · Lux · HYATT-09 · 12.12 — 4 kishi: $2,290 → $2,140 (−$150)`, plus ` · ETIHAD <name>: $1,990`
when the competitor package is matched to one of ours (`package_matches`, same date and pax).
- One message per company per pass (split only if it would exceed Telegram's limit). Sent events get `telegram_sent_at`,
  so a restart never resends. Telegram 429 -> waits `retry_after`; any other failure leaves the events for the next pass.
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
