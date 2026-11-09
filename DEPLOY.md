# Deploy

Two containers on one server: **collector** (fetches prices every 15 min, sends alerts) and **web** (the dashboard behind Caddy with automatic HTTPS). The database, auth and RLS stay in Supabase.

## Who does what

| Step | Who |
|---|---|
| Server with Docker + a subdomain pointing to it | Mentor |
| Create `collector/.env` and `deploy/.env` on the server | Developer (values from Supabase) |
| `./deploy/deploy.sh` | Developer |
| Merge PRs, make the repo private | Mentor (admin) |
| Users, roles, 2FA for admins | Mentor |
| Telegram bot and group | Mentor + developer |
| Backup schedule and an off-server copy | Mentor |

## First deploy

1. Server needs Docker with the compose plugin, `git`, `python3`. Ports 80 and 443 open. DNS A-record for the domain points to the server.
2. `git clone` the repo, then:
   ```
   cp collector/.env.example collector/.env     # fill in: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (secret key), MANDARIN_URL, SAFO_SITE_URL, ETIHAD_API_URL, ETIHAD_API_KEY, TELEGRAM_*
   cp deploy/.env.example deploy/.env           # fill in: SITE_DOMAIN, SUPABASE_URL, SUPABASE_ANON_KEY (PUBLIC key only)
   ./deploy/deploy.sh
   ```
3. `deploy.sh` runs `collector.preflight` first. It refuses to build if the key going to the browser is a secret key, or if settings are missing. It never prints key values.
4. Check: `docker compose -f deploy/docker-compose.yml ps` (collector should become `healthy` within ~15 min), open `https://<domain>`.

## Update

```
git pull && ./deploy/deploy.sh
```

## Logs

`docker compose -f deploy/docker-compose.yml logs -f collector` (logs never contain keys; errors show only the exception type).

## Backups

Supabase free plan has no automatic backups. `deploy/backup.sh` dumps the database daily (kept 14 days).

```
sudo apt install postgresql-client
cp deploy/backup.env.example deploy/backup.env && chmod 600 deploy/backup.env   # put the DB connection string in it
crontab -e   ->   10 3 * * * /opt/umra-dashboard/deploy/backup.sh >> /var/log/umra-backup.log 2>&1
```

Copy `deploy/backups/` to another machine too. Restore test: load one dump into an empty Postgres once, to be sure it works.

## CSP

The Caddyfile sends the Content-Security-Policy as **Report-Only** first. Open the site, look at the browser console for violations, fix, then rename the header to `Content-Security-Policy` to enforce.

## Secret rotation

Key leaked or person left: create a new secret key in Supabase, put it in `collector/.env`, `docker compose -f deploy/docker-compose.yml up -d collector`, then delete the old key in Supabase.
