# Supabase — database for Umra Price Monitor

SQL migrations, Row Level Security and tests. No secrets live here. Account setup: see `AUTH.md`.

## Layout
- `migrations/` — applied in filename order. Schema changes go here as new files, never through the Supabase UI.
  Never edit a migration that may already be applied; add a new one.
  1. `…_schema.sql` — tables, views (`price_current`, `unmapped_categories`)
  2. `…_functions_triggers.sql` — role helpers, audit log, manual-entry rules, new-user profile
  3. `…_rls.sql` — RLS on every table, policies, least-privilege grants
  4. `…_seed.sql` — companies, sources, category mapping (reference data only)
  5. `…_admin_requires_2fa.sql` — admin rights only in a 2FA (`aal2`) session
  6. `…_manual_packages.sql` — editors may add packages / dates a source is missing
  7. `…_profile_names_table.sql` — display names for the "who entered it" badge (never emails)
  8. `…_etihad_tier.sql`, 9. `…_safo_tiers.sql` — more category mappings
  10. `…_review_fixes.sql` — server-side who/when on manual prices and matches (validity max 30 days), new accounts
      start inactive, anonymous sessions get nothing, admin only with a verified 2FA factor, audit of SQL-editor
      changes (`audit_log.db_role`), future tables closed to `anon` / `authenticated` by default
  11. `…_rls_initplan.sql` — the same policies, evaluated once per query instead of once per row
- `tests/` — SQL tests; `run_tests.sh` applies all migrations to an empty database, then runs them.

## Roles
| Role | Can |
|---|---|
| `viewer` | read business tables |
| `editor` | viewer + add/clear manual entries, manage package matches, add manual packages/dates |
| `admin` | editor + manage profiles, sources, category map, app settings; read `audit_log`. Only in a 2FA session, and only someone with a verified 2FA factor can be given this role |

A deactivated user (`profiles.active = false`) sees nothing: every policy goes through the role helpers.
New accounts start deactivated. The collector uses the service role key (server `.env` only); it bypasses RLS,
and its writes are the only ones not recorded in `audit_log`.

## Writing a new migration
- A new table in `public` is NOT readable by `anon` / `authenticated` until you grant it (migration 10):
  `alter table … enable row level security`, add policies, then `grant select … to authenticated`.
- Call helpers in policies as `(select app.is_active_user())` / `(select app.has_role(array['editor']))`, never bare:
  test `50_review_fixes` fails on a policy that calls them per row.

## Run the tests
Needs `psql` and an **empty** Postgres 15+ database (never the production project):

```
DATABASE_URL=postgresql://user:pass@host:5432/dbname bash supabase/tests/run_tests.sh --shim
```
`--shim` creates the few Supabase pieces plain Postgres lacks (roles and their default grants, `auth.users`,
`auth.mfa_factors`, `auth.uid()`, `auth.jwt()`). Leave it out when the database is a local Supabase stack.

## Restoring a backup
The triggers (audit, server-side stamps, new-user profile) would fire again on a plain restore and add audit rows
or profiles that collide with the restored ones. Restore the data with triggers off, as Supabase recommends:
`psql … --command 'SET session_replication_role = replica' --file data.sql`.

## Not done yet
- Group quota / booked data (old `ours.js`): deliberately no table. It must not be stored here until it is decided
  who may see it.
- HTTP-level check with the real anon key against the live project, e.g.
  `curl -s "$SUPABASE_URL/rest/v1/packages?select=id" -H "apikey: $ANON_KEY"` must return `[]` (or a permission error).
