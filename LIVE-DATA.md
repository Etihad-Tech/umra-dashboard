# Live dashboard (Phase 6)

The dashboard no longer ships prices in the page. After login, `live.js` reads from Supabase
(`packages`, `departures`, `price_current`, `sources`, and the `app_settings` row `package_services`) and fills
the same structures the page always used.
Row Level Security applies: only an active signed-in user gets rows. The files `data/competitors.js`,
`data/prices.js`, `data/events.js`, `data/info.js` and `data/packages.js` are deleted.

| Piece | Where it comes from |
|---|---|
| Our packages, dates, prices | database (`B` = ETIHAD, filled by `python -m collector.run --source etihad`) |
| Competitor prices and dates | database (Mandarin, Safo collectors) |
| Photos and hotel text of our packages | `etihad-meta.js` (static, no prices) |
| Services table | Supabase `app_settings` row `package_services` (imported with `python3 -m collector.import_services`, see `DEPLOY.md` step 7; the JSON file is not in git). Live hotels/airline from the collector win for competitors. |
| "qo'lda kiritilgan" badge | `price_current.is_manual`: a price an editor typed in, shown on the card, the tiles, the date table and the chart tooltip |
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
