# Static site behind Caddy (automatic HTTPS). Only the files listed here are copied: no tests, no collector, no .env.
FROM caddy:2-alpine
ARG SUPABASE_URL
ARG SUPABASE_ANON_KEY
COPY index.html kiritish.html auth.js live.js kiritish.js support.js sw.js etihad-meta.js manifest.webmanifest /srv/
COPY vendor /srv/vendor
COPY data /srv/data
COPY icons /srv/icons
COPY images /srv/images
COPY deploy/Caddyfile /etc/caddy/Caddyfile
# config.js is generated: the browser gets ONLY the public key (deploy.sh refuses a secret key before building)
RUN test -n "$SUPABASE_URL" && test -n "$SUPABASE_ANON_KEY" \
 && printf "window.UMRA_CONFIG = { supabaseUrl: '%s', anonKey: '%s' };\n" "$SUPABASE_URL" "$SUPABASE_ANON_KEY" > /srv/config.js
