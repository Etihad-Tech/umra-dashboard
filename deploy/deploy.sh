#!/usr/bin/env bash
# Check the settings, then build and start. Safe to run again after every update:   git pull && ./deploy/deploy.sh
# Rollback:   git checkout <older commit> && ./deploy/deploy.sh      (deploy-history.log lists what ran when)
set -euo pipefail
cd "$(dirname "$0")"

die() { echo "deploy: $*" >&2; exit 1; }
command -v docker >/dev/null || die "docker is not installed (run deploy/server-setup.sh as root first)"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is missing"
command -v python3 >/dev/null || die "python3 is missing"
[ -f .env ] || die "deploy/.env is missing (cp deploy/.env.example deploy/.env and fill it in)"
[ -f ../collector/.env ] || die "collector/.env is missing (cp collector/.env.example collector/.env and fill it in)"
chmod 600 .env ../collector/.env
[ ! -f backup.env ] || chmod 600 backup.env

# refuses a secret key going to the browser and missing settings; never prints values
(cd ../collector && python3 -m collector.preflight ../deploy/.env .env)

rev="$(git -C .. rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ -n "$(git -C .. status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  echo "deploy: note - the checkout has local changes to tracked files" >&2
fi

docker compose build
docker compose up -d --remove-orphans
# wait until Caddy answers; the collector becomes "healthy" later (first successful runs, up to ~15 min)
if ! docker compose up -d --wait --wait-timeout 120 web; then
  docker compose logs --tail 60 web >&2
  die "web did not become healthy"
fi
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rev" >> deploy-history.log
docker compose ps
echo "deploy: ok ($rev)"
