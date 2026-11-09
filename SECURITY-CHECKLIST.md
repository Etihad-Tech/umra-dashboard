# Security checklist

Status: **done** = in the repo/code, **server** = needs the server, **mentor** = needs admin rights.

| # | Item | Status |
|---|---|---|
| 1 | RLS on all tables, roles viewer/editor/admin | done (migrations) |
| 2 | Admin without 2FA session acts as viewer | done |
| 3 | Browser gets only the publishable key; `deploy.sh` refuses a secret key | done |
| 4 | Collector secret key only in `collector/.env` (gitignored, chmod 600) | done / server |
| 5 | Old leaked legacy key disabled in Supabase ("Disable JWT-based API keys") | developer, after the new key works |
| 6 | Competitor/price data removed from public files | done (history still has old data) |
| 7 | Rewrite git history or accept it; make repo private | mentor |
| 8 | HTTPS + HSTS, nosniff, X-Frame-Options DENY, Referrer-Policy, Permissions-Policy, no Server header | done (Caddyfile) |
| 9 | CSP: Report-Only now, enforce after checking the console | done / server |
| 10 | Containers: non-root, read-only FS, all capabilities dropped, no-new-privileges, memory limits | done |
| 11 | Collector self-healing loop; healthcheck marks it unhealthy if data is stale | done |
| 12 | Logs never contain keys (only exception type) | done (tested) |
| 13 | Audit log for manual entries | done |
| 14 | Daily DB backup + off-server copy + restore test | script done; schedule: server/mentor |
| 15 | Server hardening: SSH keys only, firewall (22/80/443), automatic security updates | server/mentor |
| 16 | New ETIHAD API key (the first one was shown in chat) | mentor |
| 17 | Telegram bot token only in `.env`; private group | later |
| 18 | Enable 2FA for every admin; review users and roles | mentor |
