# Supabase — database for Umra Price Monitor (Phase 1)

SQL migrations, Row Level Security and tests. No secrets live here.

## Layout
- `migrations/` — applied in filename order. Schema changes go here as new files, never through the Supabase UI.
  1. `…_schema.sql` — tables, views (`price_current`, `unmapped_categories`)
  2. `…_functions_triggers.sql` — role helpers, audit log, manual-entry rules, new-user profile
  3. `…_rls.sql` — RLS on every table, policies, least-privilege grants
  4. `…_seed.sql` — companies, sources, category mapping (reference data only)
- `tests/` — SQL tests; `run_tests.sh` applies all migrations to an empty database, then runs them.

## Roles
| Role | Can |
|---|---|
| `viewer` | read business tables |
| `editor` | viewer + add/clear manual entries, manage package matches |
| `admin` | editor + manage profiles, sources, category map, app settings; read `audit_log` |

A deactivated user (`profiles.active = false`) sees nothing: every policy goes through the role helpers.
The collector uses the service role key (server `.env` only); it bypasses RLS.

## Run the tests
Needs `psql` and an **empty** Postgres 15+ database (never the production project):

```
DATABASE_URL=postgresql://user:pass@host:5432/dbname bash supabase/tests/run_tests.sh --shim
```
`--shim` creates the few Supabase pieces plain Postgres lacks (roles, `auth.users`, `auth.uid()`).
Leave it out when the database is a local Supabase stack, which already has them.

## Not in Phase 1 yet
- Group quota / booked data (old `ours.js`): no table until it is decided who may see it.
- Editor rights to add packages or departures a source is missing (Phase 5).
- HTTP-level check with the real anon key against the live project (run once the project exists).
