#!/usr/bin/env bash
# Check the settings, then build and start. Safe to run again after every update (git pull && ./deploy.sh).
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "deploy/.env is missing (copy .env.example)"; exit 1; }
[ -f ../collector/.env ] || { echo "collector/.env is missing (copy collector/.env.example)"; exit 1; }
chmod 600 .env ../collector/.env
(cd ../collector && python3 -m collector.preflight ../deploy/.env .env)
docker compose build
docker compose up -d
docker compose ps
