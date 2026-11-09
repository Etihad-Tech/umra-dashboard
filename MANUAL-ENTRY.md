# Manual entry (Phase 5)

Page: `kiritish.html` (editors and admins only; viewers see "no access"). Same login as the dashboard.

## What an editor can do
| Tab | Action | Table written |
|---|---|---|
| Narx | Enter prices for 1–4 pax and/or a sold-out flag for one departure. Valid 1–30 days (default 7). A price >30% away from the current one asks for confirmation. | `manual_entries` (insert) |
| Paket / sana | Add a new package with its first date, or a new date to an existing package. Rows are marked `is_manual = true`. | `packages`, `departures` (insert) |
| Bog'lash | Link our package (ETIHAD) to a competitor package; "Uzish" sets `active = false` (nothing is deleted). | `package_matches` |
| Yozuvlar | Active manual entries: who, when, valid until, conflict flag. "O'chirish" sets `cleared_at`. | `manual_entries` (update) |

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
2. Publish the site. The service worker is `umra-v4`, so browsers pick up the new page.
3. Give people the `editor` role (see `supabase/AUTH.md`).
