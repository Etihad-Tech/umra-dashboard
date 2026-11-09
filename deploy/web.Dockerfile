# Static site behind Caddy (automatic HTTPS). Only the files listed here are copied: no tests, no collector, no .env,
# no data files. Base image pinned by version AND digest (update both together: docker buildx imagetools inspect caddy:<ver>-alpine).
FROM caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee
ARG SUPABASE_URL
ARG SUPABASE_ANON_KEY

# Run as a fixed non-root user that owns Caddy's certificate (/data) and config (/config) folders.
# Ports 80/443 come from the ip_unprivileged_port_start sysctl (docker-compose.yml), not from a capability:
# the caddy binary carries a file capability, and with cap_drop ALL such a binary refuses to start, so it is
# re-copied here without it (cp does not copy the security.capability attribute).
RUN addgroup -S -g 10002 web \
 && adduser -S -D -H -u 10002 -G web -s /sbin/nologin web \
 && mkdir -p /data/caddy /config/caddy \
 && chown -R web:web /data /config \
 && cp /usr/bin/caddy /tmp/caddy && mv -f /tmp/caddy /usr/bin/caddy && chmod 0755 /usr/bin/caddy

COPY index.html kiritish.html auth.js live.js kiritish.js support.js sw.js sw-register.js etihad-meta.js manifest.webmanifest /srv/
COPY vendor/supabase.js vendor/supabase-js.LICENSE /srv/vendor/
COPY icons /srv/icons
COPY images /srv/images
COPY deploy/Caddyfile /etc/caddy/Caddyfile

# config.js is generated: the browser gets ONLY the public key (deploy.sh runs the preflight that refuses a secret key).
RUN test -n "$SUPABASE_URL" && test -n "$SUPABASE_ANON_KEY" \
 && ! printf '%s%s' "$SUPABASE_URL" "$SUPABASE_ANON_KEY" | grep -q "[\"'\\\\<> ]" \
 && printf "window.UMRA_CONFIG = { supabaseUrl: '%s', anonKey: '%s' };\n" "$SUPABASE_URL" "$SUPABASE_ANON_KEY" > /srv/config.js

USER web
