#!/usr/bin/env bash
# Daily database backup -> deploy/backups/umra-<UTC time>.sql.gz, kept KEEP_DAYS (14) days.
# DATA ONLY (public + auth): the schema comes from supabase/migrations in git, so a restore = migrations, then this
# file with triggers off (SET session_replication_role = replica). Steps: DEPLOY.md "Restore a backup".
# pg_dump runs in a pinned postgres:17 container, so the server needs only Docker (no apt client version mismatch;
# pg_dump 17 can dump Supabase Postgres 15 and 17).
# cron (deploy user, every day 03:10):
#   10 3 * * * $HOME/umra-dashboard/deploy/backup.sh >> $HOME/umra-backup.log 2>&1
# The dump contains password hashes and 2FA secrets: files are 600, the folder 700. Copy them OFF this server too.
set -euo pipefail
umask 077
cd "$(dirname "$0")"

PG_IMAGE="postgres:17.11-alpine@sha256:3540c0362c809df084ffac0ae69fe794515f9de92692f02178bcdce0ef0302ac"
KEEP_DAYS="${KEEP_DAYS:-14}"
ENV_FILE="backup.env"

die() { echo "backup: $*" >&2; exit 1; }
DOCKER=(docker)   # outside the docker group (a shared server): through passwordless sudo
docker info >/dev/null 2>&1 || DOCKER=(sudo -n docker)
[ -f "$ENV_FILE" ] || die "deploy/$ENV_FILE is missing (cp deploy/backup.env.example deploy/backup.env)"
chmod 600 "$ENV_FILE"
grep -q '^SUPABASE_DB_URL=' "$ENV_FILE" && die "$ENV_FILE uses the old SUPABASE_DB_URL line; use the PG* lines from backup.env.example"
for k in PGHOST PGUSER PGPASSWORD PGDATABASE; do
  grep -Eq "^${k}=.+" "$ENV_FILE" || die "$k is not set in $ENV_FILE"
done
ssl_args=(-e PGSSLMODE=require)
if grep -q '^PGSSLMODE=' "$ENV_FILE"; then
  grep -Eq '^PGSSLMODE=(require|verify-ca|verify-full)$' "$ENV_FILE" || die "PGSSLMODE must be require, verify-ca or verify-full"
  ssl_args=()
fi
[[ "$KEEP_DAYS" =~ ^[0-9]+$ ]] || die "KEEP_DAYS must be a number"

mkdir -p backups
chmod 700 backups
tmp="$(mktemp backups/.umra-XXXXXX.sql.gz.tmp)"
trap 'rm -f "$tmp"' EXIT

# --env-file: the password goes to pg_dump through the environment, not argv (not visible in ps).
# --log-driver none: the dump streams to us only, it is not copied into Docker's container log.
"${DOCKER[@]}" run --rm --log-driver none --network bridge \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user 70:70 \
  --env-file "$ENV_FILE" "${ssl_args[@]}" \
  "$PG_IMAGE" \
  pg_dump --data-only --schema=public --schema=auth --exclude-table-data=auth.schema_migrations \
  | gzip -9 > "$tmp"
gzip -t "$tmp" || die "the dump is not a valid gzip file"
first="$(gzip -dc "$tmp" 2>/dev/null | head -c 4096 || true)"
[[ "$first" == *"PostgreSQL database dump"* ]] || die "the dump looks empty"

out="backups/umra-$(date -u +%Y-%m-%d-%H%M).sql.gz"
mv "$tmp" "$out"
find backups -maxdepth 1 -type f -name 'umra-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
find backups -maxdepth 1 -type f -name '.umra-*.tmp' -mmin +1440 -delete
echo "backup ok: $out ($(du -h "$out" | cut -f1))"
