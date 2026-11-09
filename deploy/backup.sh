#!/usr/bin/env bash
# Daily database backup (public + auth schemas), kept 14 days. Needs postgresql-client (pg_dump) on the server.
# cron example (every day 03:10):   10 3 * * * /opt/umra-dashboard/deploy/backup.sh >> /var/log/umra-backup.log 2>&1
# IMPORTANT: also copy deploy/backups/ OFF this server (another machine or storage), otherwise one disk failure loses both.
set -euo pipefail
cd "$(dirname "$0")"
umask 077
[ -f backup.env ] || { echo "deploy/backup.env is missing"; exit 1; }
set -a; . ./backup.env; set +a
: "${SUPABASE_DB_URL:?SUPABASE_DB_URL is not set}"
mkdir -p backups
out="backups/umra-$(date +%F-%H%M).sql.gz"
pg_dump --no-owner --no-privileges --schema=public --schema=auth "$SUPABASE_DB_URL" | gzip > "$out.tmp"
mv "$out.tmp" "$out"
find backups -name 'umra-*.sql.gz' -mtime +14 -delete
echo "backup ok: $out ($(du -h "$out" | cut -f1))"
