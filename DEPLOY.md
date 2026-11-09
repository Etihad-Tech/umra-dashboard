# Deploy (VPS + Docker)

From a blank Ubuntu 24.04 VPS and a domain to: site live with HTTPS, collector running, daily backups, updates,
rollback and restore. Two containers: **web** (Caddy: the static dashboard, automatic HTTPS, security headers) and **collector**
(fetches prices every 15 min, sends Telegram alerts). Database, login and RLS stay in Supabase.

Commands marked `[laptop]`, `[root]` or `[deploy]` run there. Replace `prices.example.com` with your domain.

## 0. Before you start

- A VPS with Ubuntu 24.04 (1 vCPU / 2 GB RAM is plenty), its public IPv4, root access (provider key or password).
- A (sub)domain you can point to it.
- Your SSH public key: `[laptop] cat ~/.ssh/id_ed25519.pub` (none? `ssh-keygen -t ed25519`).
- Supabase project with every file in `supabase/migrations/` applied in order (see `supabase/README.md`), plus:
  publishable key, secret key, database password; ETIHAD API URL + key; competitor URLs; Telegram bot token.

## 1. DNS

Create an **A** record `prices.example.com -> <server IPv4>` (an AAAA record only if the server really has IPv6).
Wait until `[laptop] dig +short prices.example.com` prints the IP. Caddy can get the certificate only after that.

## 2. Get the code onto the server

```
[laptop] ssh root@<server-ip>
[root]   apt-get update && apt-get install -y git
```
Public repository:
```
[root]   git clone https://github.com/Etihad-Tech/umra-dashboard.git /opt/umra-dashboard
```
Private repository (recommended): a read-only deploy key.
```
[root]   ssh-keygen -t ed25519 -N '' -C umra-server -f /root/.ssh/umra_repo && cat /root/.ssh/umra_repo.pub
         # GitHub -> repo -> Settings -> Deploy keys -> Add (read-only) -> paste the line above
[root]   GIT_SSH_COMMAND='ssh -i /root/.ssh/umra_repo -o IdentitiesOnly=yes' \
           git clone git@github.com:Etihad-Tech/umra-dashboard.git /opt/umra-dashboard
```

## 3. Prepare the server (once; safe to re-run)

```
[root]   SSH_PUBKEY="ssh-ed25519 AAAA...your key... you@laptop" bash /opt/umra-dashboard/deploy/server-setup.sh
```
It installs Docker from Docker's own apt repository, creates the user `deploy` (groups docker + sudo), opens only
22/tcp, 80/tcp, 443/tcp, 443/udp in ufw, turns on automatic security updates, and sets SSH to keys only with no root
login. It asks for a password for `deploy` (used only for `sudo`). It **refuses** to touch SSH while `deploy` has no
key, so you cannot lock yourself out. Exit code 0 = everything done; 2 = read the WARNING lines and run it again.

Private repository only, give `deploy` the deploy key:
```
[root]   install -o deploy -g deploy -m 600 /root/.ssh/umra_repo /home/deploy/.ssh/umra_repo
[root]   sudo -u deploy git -C /opt/umra-dashboard config core.sshCommand 'ssh -i ~/.ssh/umra_repo -o IdentitiesOnly=yes'
```
**Keep the root session open** and check in a new terminal: `[laptop] ssh deploy@<server-ip>` then `docker ps`.
From now on work as `deploy`.

## 4. Settings (secrets stay on the server)

```
[deploy] cd /opt/umra-dashboard
[deploy] cp collector/.env.example collector/.env && nano collector/.env
         # SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (secret key), MANDARIN_URL, SAFO_SITE_URL,
         # ETIHAD_API_URL, ETIHAD_API_KEY, TELEGRAM_BOT_TOKEN
[deploy] cp deploy/.env.example deploy/.env && nano deploy/.env
         # SITE_DOMAIN=prices.example.com, SUPABASE_URL, SUPABASE_ANON_KEY = the PUBLISHABLE key only
```
Both files are gitignored; `deploy.sh` makes them `chmod 600`.

## 5. Start

```
[deploy] ./deploy/deploy.sh
```
It runs `collector.preflight` first (refuses a secret key in the browser config and missing settings; never prints
values), builds both images, starts them, waits until the web container is healthy, and ends with `deploy: ok (<commit>)`.

## 6. Check

```
[deploy] docker compose -f deploy/docker-compose.yml ps        # web: healthy now; collector: healthy within ~15 min
[laptop] curl -sI https://prices.example.com | grep -iE 'strict-transport|content-security|x-frame'
[deploy] docker compose -f deploy/docker-compose.yml logs -f collector    # Ctrl+C to stop
```
Open `https://prices.example.com`, log in, the prices appear. Editors open `kiritish.html` from the bar at the top.

## 7. Services table data (hotels, meals, train, transfer)

This data is NOT in git and NOT a public file. It lives in Supabase (`app_settings` row `package_services`) and the
dashboard reads it after login. Keep the JSON file privately (for example in a password manager) and import it:
```
[laptop] ssh deploy@<server-ip> 'install -d -m 700 /opt/umra-dashboard/private'
[laptop] scp private/services.json deploy@<server-ip>:/opt/umra-dashboard/private/services.json
[deploy] cd /opt/umra-dashboard/collector
[deploy] python3 -m collector.import_services ../private/services.json --dry-run     # checks the file only
[deploy] python3 -m collector.import_services ../private/services.json --env .env    # uploads it
```
Run the same two commands after every change of the file. The database must let active users read that one row
(policy on `app_settings` for key `package_services`); without it only admins see the services table.

## 8. Daily backup

```
[deploy] cp deploy/backup.env.example deploy/backup.env && chmod 600 deploy/backup.env && nano deploy/backup.env
         # Supabase -> Connect -> "Session pooler": PGHOST, PGPORT, PGUSER (postgres.<ref>), PGPASSWORD, PGDATABASE
[deploy] ./deploy/backup.sh                                     # once by hand: "backup ok: backups/umra-....sql.gz"
[deploy] (crontab -l 2>/dev/null | grep -v 'deploy/backup.sh'; \
          echo '10 3 * * * /opt/umra-dashboard/deploy/backup.sh >> $HOME/umra-backup.log 2>&1') | crontab -
[deploy] crontab -l
```
`pg_dump` runs in a pinned `postgres:17` container (no client to install), the password is passed through the
environment (not visible in `ps`), SSL is required, files are `600` in a `700` folder, kept 14 days.
The backup is **data only** (schemas `public` and `auth`): the schema is `supabase/migrations/` in git.
The dumps contain password hashes and 2FA secrets.

**Off-server copy** (one disk failure must not lose both): from another machine, daily:
`rsync -a deploy@<server-ip>:/opt/umra-dashboard/deploy/backups/ ~/umra-backups/`

**Restore test** (once, then after big changes): do section 11 into a temporary free Supabase project, check the
row counts in its SQL editor, then delete that project.

## 9. Update

```
[deploy] cd /opt/umra-dashboard && git pull --ff-only && ./deploy/deploy.sh
```
If the update adds files under `supabase/migrations/`, apply them in Supabase **before** running `deploy.sh`.
Browsers pick up the new version on the next load (every file except photos/icons is revalidated; the service
worker cache name changes with each release).

## 10. Rollback

```
[deploy] tail deploy/deploy-history.log                 # time + commit of every successful deploy
[deploy] git checkout <previous commit> && ./deploy/deploy.sh
```
Back to normal later: `git checkout main && git pull --ff-only && ./deploy/deploy.sh`.
This rolls back the web and collector code only, not database migrations. Never run `docker compose down -v`:
the `caddy_data` volume holds the TLS certificates (Let's Encrypt has rate limits).

## 11. Restore a backup

The database triggers (audit log, server-side stamps, the profile created for a new user) must NOT fire again during
a restore, or they add audit rows and profiles that collide with the restored ones. So the data is loaded with
triggers off (`SET session_replication_role = replica`, as in `supabase/README.md` "Restoring a backup"), after the
rows the migrations seed are emptied, all in one transaction (any error = nothing changed). `deploy/restore.sh` does
exactly that.

1. Create a NEW Supabase project (the target; restoring over the live project with its users is refused).
2. Apply every file in `supabase/migrations/` in order, from the commit that was live when the backup was made
   (`deploy/deploy-history.log`).
3. Put the target's session-pooler values in `deploy/restore.env` (same lines as `backup.env.example`, `chmod 600`).
4. Load (it shows the target and asks you to type its host name):
   ```
   [deploy] ./deploy/restore.sh deploy/backups/umra-<date>.sql.gz deploy/restore.env
   ```
5. Point the app at the new project: new URL and keys in `deploy/.env` and `collector/.env`, then `./deploy/deploy.sh`.
   Passwords and 2FA factors are restored; everyone logs in again.

## Logs

`docker compose -f deploy/docker-compose.yml logs --tail 100 web` / `... collector`. Logs never contain keys
(errors show only the exception type). Docker rotates them (10 MB x 5 per container).

## Security headers and CSP

Set in `deploy/Caddyfile` and **enforced**: HSTS, nosniff, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
Permissions-Policy, COOP, no `Server` header, and a Content-Security-Policy that allows only this site, the exact
Supabase URL, Google Fonts, and the three jsDelivr files (with SRI) the page runtime `support.js` loads. The runtime
needs `'unsafe-eval'`. Pages must not contain inline scripts or `on...=` attributes (use `sc-camel-on-click` in the
template); `tests-web/deploy.test.js` checks this. If `support.js` is replaced, update the three URLs in the Caddyfile.

## Secret rotation

Key leaked or person left: create a new secret key in Supabase, put it in `collector/.env`, run
`docker compose -f deploy/docker-compose.yml up -d collector`, then delete the old key in Supabase.
Database password changed: update `deploy/backup.env`. Publishable key changed: update `deploy/.env`, run `./deploy/deploy.sh`.
