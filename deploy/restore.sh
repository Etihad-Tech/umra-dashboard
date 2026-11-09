#!/usr/bin/env bash
# Restore a data backup made by backup.sh into a database that already has EVERY migration applied
# (normally a NEW Supabase project; the steps are in DEPLOY.md "Restore a backup").
#
#   ./deploy/restore.sh deploy/backups/umra-YYYY-MM-DD-HHMM.sql.gz deploy/restore.env
#
# restore.env = the TARGET database, same PG* lines as backup.env.example (chmod 600, gitignored).
# One transaction, as supabase/README.md "Restoring a backup" says: triggers off (SET session_replication_role =
# replica, so the audit / stamp / new-user-profile triggers do not fire again), empty every table in schema public
# (removes the rows the migrations seeded), load the data. Any error -> rolled back, nothing changes.
# The auth tables are NOT emptied: in a new project they are empty; in a project with users the load stops on the
# first duplicate and nothing is changed.
set -euo pipefail
umask 077

PG_IMAGE="postgres:17.11-alpine@sha256:3540c0362c809df084ffac0ae69fe794515f9de92692f02178bcdce0ef0302ac"
die() { echo "restore: $*" >&2; exit 1; }
DOCKER=(docker)   # outside the docker group (a shared server): through passwordless sudo
docker info >/dev/null 2>&1 || DOCKER=(sudo -n docker)

[ $# -eq 2 ] || die "usage: $0 <backup .sql.gz> <target env file>"
dump="$1"
env_file="$2"
[ -f "$dump" ] || die "$dump not found"
[ -f "$env_file" ] || die "$env_file not found (cp deploy/backup.env.example deploy/restore.env and fill in the TARGET)"
chmod 600 "$env_file"
gzip -t "$dump" || die "$dump is not a valid gzip file"
for k in PGHOST PGUSER PGPASSWORD PGDATABASE; do
  grep -Eq "^${k}=.+" "$env_file" || die "$k is not set in $env_file"
done
ssl_args=(-e PGSSLMODE=require)
if grep -q '^PGSSLMODE=' "$env_file"; then
  grep -Eq '^PGSSLMODE=(require|verify-ca|verify-full)$' "$env_file" || die "PGSSLMODE must be require, verify-ca or verify-full"
  ssl_args=()
fi
host="$(grep -E '^PGHOST=' "$env_file" | head -n 1 | cut -d= -f2-)"
db="$(grep -E '^PGDATABASE=' "$env_file" | head -n 1 | cut -d= -f2-)"

echo "This EMPTIES every table in schema public of database '$db' on $host"
echo "and loads $(basename "$dump") into it."
if [ "${RESTORE_CONFIRM:-}" != "$host" ]; then
  [ -t 0 ] || die "no terminal to ask: set RESTORE_CONFIRM=$host to confirm"
  read -r -p "Type the host name to confirm: " answer
  [ "$answer" = "$host" ] || die "not confirmed, nothing changed"
fi

empty_public="DO \$\$ DECLARE t text; BEGIN
  FOR t IN SELECT format('%I.%I', schemaname, tablename) FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE 'TRUNCATE ' || t || ' CASCADE';
  END LOOP;
END \$\$"

gzip -dc "$dump" | "${DOCKER[@]}" run --rm -i --log-driver none --network bridge \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user 70:70 \
  --env-file "$env_file" "${ssl_args[@]}" \
  "$PG_IMAGE" \
  psql -X -q -o /dev/null --single-transaction -v ON_ERROR_STOP=1 \
    --command 'SET client_min_messages = warning' \
    --command 'SET session_replication_role = replica' \
    --command "$empty_public" \
    --file -
echo "restore ok: $(basename "$dump") -> $db on $host"
