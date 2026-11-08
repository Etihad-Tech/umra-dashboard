#!/usr/bin/env bash
# Applies every migration to an EMPTY database, then runs the SQL tests.
#   Local / CI (plain Postgres):  DATABASE_URL=postgresql://... supabase/tests/run_tests.sh --shim
#   Supabase CLI local stack:     DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres supabase/tests/run_tests.sh
# Never point this at the production project: the tests create and roll back data, but the migrations are real.
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL to an empty Postgres database}"
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
run() { psql "$DATABASE_URL" -X -q -v ON_ERROR_STOP=1 "$@"; }

if [ "${1:-}" = "--shim" ]; then
  echo "== shim (plain Postgres only)"; run -f "$here/ci_shim.sql"
fi

for f in "$root"/migrations/*.sql; do
  echo "== migrate $(basename "$f")"; run -f "$f"
done

for f in "$here"/[1-9]*_*.sql; do
  echo "== test $(basename "$f")"; (cd "$here" && run -f "$(basename "$f")")
done

echo "ALL TESTS PASSED"
