# Manual entry (Phase 5)

Page: `kiritish.html` (editors and admins only; viewers see "no access"). Same login as the dashboard.

## What an editor can do
| Tab | Action | Table written |
|---|---|---|
| Narxlar | Edit prices for 1–4 pax and/or the sold-out flag of many dates in one table. Valid 7/14/30 days (default 7). | `manual_entries` (insert) |
| Paket / sana | Add a new package with its first date, or a new date to an existing package. Rows are marked `is_manual = true`. | `packages`, `departures` (insert) |
| Bog'lash | Link our package (ETIHAD) to a competitor package; "Uzish" sets `active = false` (nothing is deleted). | `package_matches` |
| Yozuvlar | Active manual entries: who, when, valid until, conflict flag. "O'chirish" sets `cleared_at`. | `manual_entries` (update) |

## Price table (Phase 6b, replaces the old price form)
1. Tap a company, then a package (the last choice is remembered).
2. Every upcoming date is a row; the cells already contain the CURRENT prices (green border = a manual price).
3. Change what you need directly in the table. Changed cells turn gold; an empty cell or the same price means "unchanged".
4. Tick "Tugagan" to mark a date sold out (untick to reopen it).
5. The bar at the bottom says how many changes will be saved; pick how long the price is valid (7/14/30 days), press "Saqlash".

"One price for all dates": type it above the table, press "Hammasiga qo'yish" — it only fills the cells (sold-out dates are skipped); nothing is saved until "Saqlash".
A price more than 30% away from the current one asks for confirmation. While a save is on its way the button stays
disabled (no double insert). "Upcoming" dates use today's date in Tashkent. On the dashboard a manual price carries
the badge «qo'lda kiritilgan». Service worker is `umra-v7`.

## Rules enforced by the database (migration 6), not by the page
- Editors can only INSERT manual rows (`is_manual = true`) and cannot edit or delete automatic ones.
- Editors cannot change `company_id` or `is_manual` of an existing row.
- Every insert/update is written to `audit_log` (who, when, before/after).
- Manual entries never overwrite collector data; both are stored and `price_current` shows the manual one until it expires or is cleared.

## Collector behaviour
- Manual departures and packages are never marked "removed".
- If the source later shows a price different from an active manual price, an event `manual_conflict` is created and sent to Telegram with both prices.

## Deploy order
1. Review and apply `supabase/migrations/20261108000006_manual_packages.sql` in the Supabase SQL Editor.
2. Publish the site (`DEPLOY.md`, "Update"). Every page file is revalidated on load and the service worker is `umra-v7`, so browsers pick up the new page.
3. Give people the `editor` role (see `supabase/AUTH.md`).
