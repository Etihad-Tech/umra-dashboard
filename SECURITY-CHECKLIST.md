# Security checklist

Status: **done** = in the repo and checked (how: in brackets), **server** = needs the server, **mentor** = needs admin
rights. Nothing is marked done that only exists on paper.

| # | Item | Status |
|---|---|---|
| 1 | RLS on all tables, roles viewer/editor/admin | done (migrations, `supabase/tests`) |
| 2 | Admin without a 2FA session acts as viewer | done (migration 5, tests) |
| 3 | Browser gets only the publishable key; `deploy.sh` refuses a secret key before building | done (`collector.preflight`) |
| 4 | Collector secret key only in `collector/.env` (gitignored, `chmod 600` by `deploy.sh`, never in an image) | done |
| 5 | Old leaked legacy key disabled in Supabase ("Disable JWT-based API keys") | developer, after the new key works |
| 6 | No competitor/price/services data in public files: prices come from Supabase after login; the services table data was moved from `data/services.js` into Supabase (`app_settings` row, imported with `collector.import_services`) | done (`tests-web`; old data still in git history, see 7) |
| 7 | Rewrite git history or accept it; make the repository private | mentor |
| 8 | HTTPS + HSTS, nosniff, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, Permissions-Policy, COOP, no `Server` header | done (headers in `deploy/Caddyfile`, checked with curl against the built image; TLS by the server's nginx + certbot, `deploy/nginx-site.conf`; web published on 127.0.0.1 only) |
| 9 | Content-Security-Policy **enforced**: only self, the exact Supabase URL, Google Fonts and three SRI-pinned jsDelivr files; no inline scripts or inline event handlers; `object-src`/`base-uri`/`frame-ancestors` `'none'`. Known limits: `'unsafe-eval'` (page runtime compiles its template) and inline styles | done (`tests-web/deploy.test.js`; headless browser: 0 violations logged out and logged in) |
| 10 | Containers: non-root (web uid 10002, collector uid 10001), read-only root FS + tmpfs, `cap_drop: ALL` (web gets low ports from a sysctl, no capability), `no-new-privileges`, memory and pids limits, collector exposes no port, `init` for the collector | done (`docker exec ... id` = 10002, CapEff 0) |
| 11 | Pinned images and dependencies: Caddy and the backup's postgres by version + digest; collector base image by digest and Python packages with hashes | done |
| 12 | Collector self-healing loop; healthcheck marks it unhealthy if data is stale; web healthcheck | done |
| 13 | Logs never contain keys (only exception type) | done (tested) |
| 14 | Audit log for manual entries | done |
| 15 | Service worker caches only the app shell (never Supabase responses); logout clears all caches; deploys reach users at once (`Cache-Control: no-cache` except photos/icons, precache bypasses the HTTP cache) | done (`tests-web`) |
| 16 | Daily DB backup (data only): password not on the command line, SSL required, files 600 / folder 700, 14 days; `restore.sh` loads it with triggers off in one transaction | scripts done (backup + restore run end to end on a local Postgres 17 with SSL, all migrations: identical rows, no audit/profile collisions); cron: server (`DEPLOY.md` step 8) |
| 17 | Off-server backup copy + one restore test into a temporary Supabase project | server/mentor |
| 18 | Server hardening: SSH keys only, no root login, ufw (22, 80, 443 tcp), automatic security updates | fresh server: `deploy/server-setup.sh`. Shared server: belongs to whoever runs that server (the script refuses to run there) |
| 19 | Services table readable by active users only: needs a read policy for `app_settings` key `package_services` | database change pending (until then only admins see the services table) |
| 20 | New ETIHAD API key (the first one was shown in chat) | mentor |
| 21 | Telegram bot token only in `.env`; private group | later |
| 22 | Enable 2FA for every admin; review users and roles | mentor |
