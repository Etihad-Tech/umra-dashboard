# Live dashboard (Phase 6)

The dashboard no longer ships prices in the page. After login, `live.js` reads from Supabase
(`packages`, `departures`, `price_current`, `sources`) and fills the same structures the page always used.
Row Level Security applies: only an active signed-in user gets rows. The files `data/competitors.js`,
`data/prices.js`, `data/events.js`, `data/info.js` and `data/packages.js` are deleted.

| Piece | Where it comes from |
|---|---|
| Our packages, dates, prices | database (`B` = ETIHAD, filled by `python -m collector.run --source etihad`) |
| Competitor prices and dates | database (Mandarin, Safo collectors) |
| Photos and hotel text of our packages | `etihad-meta.js` (static, no prices) |
| Services table | `data/services.js` (static, no prices) |
| "Today" | real date in Asia/Tashkent (UTC+5) |
| Freshness pill | the OLDEST `sources.last_ok_at`; a source with 3+ failures is named |
| Refresh | every 5 minutes and when the tab becomes visible again |

## Comparing with competitors needs a tier
The page compares only packages of the same tier (Premium / Lux / Comfort+Econom). A competitor package whose
tier is empty is not compared. See what is missing:

    cd collector && set -a && source .env && set +a && python -m collector.inspect_db

Fix by adding the competitor's raw category (upper case) to `category_map`, for example:

    insert into public.category_map (raw, tier, note) values ('SOME RAW NAME', 'lux', 'Safo') on conflict (raw) do nothing;

Then run the collector once; packages pick up the tier on the next run.


## Fourth company: Hikmat Umra (Phase 8)
The dashboard now shows ETIHAD, SAFO, MANDARIN and HIKMAT. Hikmat's public site has no dated prices:
- It publishes one price "from X $ per person" per package, and the group departure dates (Wednesdays).
- The collector (`collector/collector/hikmat.py`) stores the "from" price as the price for **4 people** (the cheapest room type).
  Prices for 1, 2 and 3 people, and exact prices per date, are entered by hand on `kiritish.html` (a manual entry wins).
- Most Hikmat packages are 13+ nights, ours 5-9, so only packages within 2 nights of ours (today: Elita, 7 nights) are compared.
- Set `HIKMAT_SITE_URL` in `collector/.env`. If it is not set, `--source all` skips Hikmat (with a warning).
- Run migration `supabase/migrations/20261108000010_hikmat.sql` first (adds company `H`, its source and the tier of `ELITA`).
- Tiers of MAXSUS, BARAKA, AFZAL and ISTANBUL + UMRA are not decided yet (tier NULL = not compared).
