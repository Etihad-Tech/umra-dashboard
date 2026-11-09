# Deploy (Docker behind the server's nginx)

From a server with Docker and nginx to: site live with HTTPS, collector running, daily backups, updates, rollback and
restore. Two containers:
- **web** (`etihad-narx-web`): Caddy serves the static dashboard with the security headers and the CSP, on
  `127.0.0.1:WEB_PORT` only (default 8744);
- **collector** (`etihad-narx-collector`): fetches prices every 15 min and sends Telegram alerts.

The server's nginx terminates TLS in front of the web container, with a certificate from certbot, the same way as
for the other apps on that server. Database, login and RLS stay in Supabase.

Commands marked `[laptop]` or `[server]` run there. `[server]` is the login user: on the shared server that is
`ubuntu`, which has passwordless sudo and is not in the docker group. The scripts call `sudo docker` by themselves
when they need it. The checkout lives in `~/umra-dashboard`. Replace `narx-xxxxxxxxxxxx.<server-ip>.sslip.io` with
the real site name.

## 0. Before you start

- The server needs Ubuntu 24.04 with Docker + the compose plugin, nginx, certbot (`python3-certbot-nginx`), git,
  python3 and curl. The shared server has all of them. A brand-new empty VPS gets them from section "Fresh server" at
  the end.
- A site name. Either a real (sub)domain with an **A** record pointing to the server, or no DNS at all:
  `<word>-<12 random letters/digits>.<server-ip>.sslip.io`, which resolves to the IP by itself (that is how the
  other apps on the shared server are named). A random part keeps the name out of casual scans. It is NOT protection:
  certificate logs are public, and the login is what protects the data.
- A free loopback port: check `[server] sudo ss -tlnp | grep 127.0.0.1`. 8742 and 8743 are taken on the shared
  server; 8744 is the default.
- A Supabase project with every file in `supabase/migrations/` applied in order (see `supabase/README.md`), plus:
  - the publishable key, the secret key and the database password;
  - the ETIHAD API URL and key;
  - the competitor URLs;
  - a Telegram bot token.

## 1. Get the code onto the server (read-only deploy key)

```
[server] ssh-keygen -t ed25519 -N '' -C 'umra-dashboard deploy key' -f ~/.ssh/id_ed25519_umrah_narx
[server] printf '\nHost github-narx\n    HostName github.com\n    User git\n    IdentityFile ~/.ssh/id_ed25519_umrah_narx\n    IdentitiesOnly yes\n' >> ~/.ssh/config
[server] cat ~/.ssh/id_ed25519_umrah_narx.pub
         # GitHub -> repo -> Settings -> Deploy keys -> Add, NOT "allow write access" -> paste the line above
[server] git clone github-narx:Etihad-Tech/umra-dashboard.git ~/umra-dashboard
```

## 2. Settings (secrets stay on the server)

```
[server] cd ~/umra-dashboard
[server] cp collector/.env.example collector/.env && nano collector/.env
         # SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (secret key), MANDARIN_URL, SAFO_SITE_URL,
         # ETIHAD_API_URL, ETIHAD_API_KEY, TELEGRAM_BOT_TOKEN
[server] cp deploy/.env.example deploy/.env && nano deploy/.env
         # SITE_DOMAIN=<site name>, WEB_PORT=8744, SUPABASE_URL, SUPABASE_ANON_KEY = the PUBLISHABLE key only
```
Both files are gitignored, and `deploy.sh` makes them `chmod 600`. Type the keys in on the server. Never paste
them into a chat or a ticket.

## 3. Start

```
[server] ./deploy/deploy.sh
```
What it does:
1. Runs `collector.preflight`, which refuses a secret key in the browser config, missing settings or a bad port.
   It never prints values.
2. Builds both images and starts them.
3. Waits until the web container is healthy and `127.0.0.1:WEB_PORT` answers.
4. Ends with `deploy: ok (<commit>)`.

## 4. nginx + HTTPS (once)

```
[server] D=narx-xxxxxxxxxxxx.<server-ip>.sslip.io; P=8744          # same values as in deploy/.env
[server] sed -e "s/__SITE_DOMAIN__/$D/g" -e "s/__WEB_PORT__/$P/g" deploy/nginx-site.conf \
           | sudo tee /etc/nginx/sites-available/narx.conf >/dev/null
[server] sudo ln -s /etc/nginx/sites-available/narx.conf /etc/nginx/sites-enabled/narx.conf
[server] sudo nginx -t && sudo systemctl reload nginx                 # -t first: nginx serves the other apps too
[server] sudo certbot --nginx -d "$D" --redirect
```
certbot adds the `listen 443 ssl` lines and the HTTP->HTTPS redirect to the installed copy, and renews the
certificate by itself. Do **not** run the `sed | tee` line again later: it would remove certbot's lines. If
`deploy/nginx-site.conf` changes, edit `/etc/nginx/sites-available/narx.conf` by hand, then run `sudo nginx -t` and
reload. nginx adds no headers of its own; all of them come from Caddy.

## 5. Check

```
[server] sudo docker compose -f deploy/docker-compose.yml ps     # web: healthy now; collector: healthy within ~15 min
[laptop] curl -sI https://<site name> | grep -iE 'strict-transport|content-security|x-frame'
[server] sudo docker compose -f deploy/docker-compose.yml logs -f collector    # Ctrl+C to stop
```
Open `https://<site name>`, log in, and the prices appear. Editors open `kiritish.html` from the bar at the top.

## 6. Services table data (hotels, meals, train, transfer)

This data is NOT in git and NOT a public file. It lives in Supabase (`app_settings` row `package_services`), and the
dashboard reads it after login. Keep the JSON file privately (for example in a password manager) and import it:
```
[laptop] ssh <user>@<server-ip> 'install -d -m 700 ~/umra-dashboard/private'
[laptop] scp private/services.json <user>@<server-ip>:umra-dashboard/private/services.json
[server] cd ~/umra-dashboard/collector
[server] python3 -m collector.import_services ../private/services.json --dry-run     # checks the file only
[server] python3 -m collector.import_services ../private/services.json --env .env    # uploads it
```
Run the same two commands after every change of the file. Migration `20261109000012` lets active users read that one
row; without it only admins see the services table.

## 7. Daily backup

```
[server] cp deploy/backup.env.example deploy/backup.env && chmod 600 deploy/backup.env && nano deploy/backup.env
         # Supabase -> Connect -> "Session pooler": PGHOST, PGPORT, PGUSER (postgres.<ref>), PGPASSWORD, PGDATABASE
[server] ./deploy/backup.sh                                     # once by hand: "backup ok: backups/umra-....sql.gz"
[server] (crontab -l 2>/dev/null | grep -v 'deploy/backup.sh'; \
          echo '10 3 * * * $HOME/umra-dashboard/deploy/backup.sh >> $HOME/umra-backup.log 2>&1') | crontab -
[server] crontab -l
```
How the backup is made:
- `pg_dump` runs in a pinned `postgres:17` container, so there is no client to install.
- The password is passed through the environment, so it is not visible in `ps`.
- SSL is required.
- Files are `600` in a `700` folder and are kept 14 days.

The backup is **data only** (schemas `public` and `auth`); the schema is `supabase/migrations/` in git. The dumps
contain password hashes and 2FA secrets.

**Off-server copy** (one disk failure must not lose both). From another machine, daily:
`rsync -a <user>@<server-ip>:umra-dashboard/deploy/backups/ ~/umra-backups/`

**Restore test** (once, then after big changes): do section 10 into a temporary free Supabase project, check the
row counts in its SQL editor, then delete that project.

## 8. Update

```
[server] cd ~/umra-dashboard && git pull --ff-only && ./deploy/deploy.sh
```
If the update adds files under `supabase/migrations/`, apply them in Supabase **before** running `deploy.sh`.
Browsers pick up the new version on the next load: every file except photos and icons is revalidated, and the
service worker cache name changes with each release.

## 9. Rollback

```
[server] tail deploy/deploy-history.log                 # time + commit of every successful deploy
[server] git checkout <previous commit> && ./deploy/deploy.sh
```
To return to normal later: `git checkout main && git pull --ff-only && ./deploy/deploy.sh`.
This rolls back the web and collector code only, not database migrations.

## 10. Restore a backup

The database triggers (audit log, server-side stamps, the profile created for a new user) must NOT fire again during
a restore. If they do, they add audit rows and profiles that collide with the restored ones. So the data is loaded
with triggers off (`SET session_replication_role = replica`, as in `supabase/README.md` "Restoring a backup").
Before that, the rows the migrations seed are emptied. All of it runs in one transaction, so any error means nothing
changed. `deploy/restore.sh` does exactly that.

1. Create a NEW Supabase project as the target. Restoring over the live project with its users is refused.
2. Apply every file in `supabase/migrations/` in order, from the commit that was live when the backup was made
   (`deploy/deploy-history.log`).
3. Put the target's session-pooler values in `deploy/restore.env` (same lines as `backup.env.example`, `chmod 600`).
4. Load. The script shows the target and asks you to type its host name:
   ```
   [server] ./deploy/restore.sh deploy/backups/umra-<date>.sql.gz deploy/restore.env
   ```
5. Point the app at the new project: put the new URL and keys in `deploy/.env` and `collector/.env`, then run
   `./deploy/deploy.sh`. Passwords and 2FA factors are restored; everyone logs in again.

## Logs

`sudo docker compose -f deploy/docker-compose.yml logs --tail 100 web` / `... collector`. Logs never contain keys:
errors show only the exception type. Docker rotates them (10 MB x 5 per container). nginx logs:
`/var/log/nginx/access.log` and `error.log`.

## Security headers and CSP

The headers are set in `deploy/Caddyfile`, and they are **enforced**:
- HSTS, nosniff, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, Permissions-Policy, COOP, and no Caddy
  `Server` header (nginx has `server_tokens off`);
- a Content-Security-Policy that allows only this site, the exact Supabase URL, Google Fonts, and the three jsDelivr
  files (with SRI) that the page runtime `support.js` loads. The runtime needs `'unsafe-eval'`.

Pages must not contain inline scripts or `on...=` attributes; use `sc-camel-on-click` in the template.
`tests-web/deploy.test.js` checks this. If `support.js` is replaced, update the three URLs in the Caddyfile.

## Secret rotation

- **Key leaked or a person left:** create a new secret key in Supabase and put it in `collector/.env`. Run
  `sudo docker compose -f deploy/docker-compose.yml up -d collector`, then delete the old key in Supabase.
- **Database password changed:** update `deploy/backup.env`.
- **Publishable key changed:** update `deploy/.env` and run `./deploy/deploy.sh`.

## Fresh server (nothing else on it)

On a brand-new Ubuntu 24.04 VPS that hosts nothing else, run this once as root before step 1:
```
[root]   apt-get update && apt-get install -y git && git clone https://github.com/Etihad-Tech/umra-dashboard.git /tmp/umra
[root]   SSH_PUBKEY="ssh-ed25519 AAAA...your key... you@laptop" bash /tmp/umra/deploy/server-setup.sh
```
It does the following:
- installs Docker from Docker's own apt repository, plus nginx and certbot;
- creates the user `deploy` (groups docker + sudo);
- opens only 22/tcp, 80/tcp and 443/tcp in ufw;
- turns on automatic security updates;
- sets SSH to keys only, with no root login.

It **refuses** to run when other nginx sites or other containers already exist: on a shared server it would change
ufw and sshd for every app there. It also refuses to touch SSH while `deploy` has no key. Keep the root session open
and test `ssh deploy@<server-ip>` in a new terminal. Then continue from step 1 as `deploy`.
