// Static checks of the deploy files: the security settings must not quietly fall back.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const read = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8');

function csp() {
  const m = /^\s*Content-Security-Policy\s+"([^"]+)"/m.exec(read('deploy/Caddyfile'));
  assert.ok(m, 'an ENFORCED Content-Security-Policy header');
  const out = {};
  m[1].split(';').map((d) => d.trim()).filter(Boolean).forEach((d) => { const [k, ...v] = d.split(/\s+/); out[k] = v; });
  return out;
}

test('CSP is enforced and tight: no inline scripts, only the exact CDN files the page runtime loads', () => {
  const caddy = read('deploy/Caddyfile');
  assert.ok(!/Content-Security-Policy-Report-Only/.test(caddy), 'not Report-Only');
  const c = csp();
  const cdn = [...new Set(read('support.js').match(/https:\/\/cdn\.jsdelivr\.net\/npm\/[^"'`\s]+/g))].sort();
  assert.ok(cdn.length >= 1);
  assert.deepStrictEqual(c['script-src'].slice().sort(), ["'self'", "'unsafe-eval'"].concat(cdn).sort());
  assert.ok(!c['script-src'].includes("'unsafe-inline'"));
  assert.deepStrictEqual(c['connect-src'], ["'self'", '{$SUPABASE_URL}', 'https://fonts.googleapis.com', 'https://fonts.gstatic.com']);
  assert.ok(!Object.values(c).flat().some((s) => s.includes('*')), 'no wildcard sources');
  assert.deepStrictEqual(c['object-src'], ["'none'"]);
  assert.deepStrictEqual(c['base-uri'], ["'none'"]);
  assert.deepStrictEqual(c['frame-ancestors'], ["'none'"]);
  assert.deepStrictEqual(c['form-action'], ["'self'"]);
  assert.deepStrictEqual(c['default-src'], ["'self'"]);
  for (const h of ['Strict-Transport-Security', 'X-Content-Type-Options "nosniff"', 'X-Frame-Options "DENY"', 'Referrer-Policy']) {
    assert.ok(caddy.includes(h), h);
  }
});

test('pages have no inline scripts or inline event handlers (the CSP would block them)', () => {
  for (const f of ['index.html', 'kiritish.html']) {
    const html = read(f);
    const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)([^>]*)>/g)].filter((m) => !/type="text\/x-dc"/.test(m[1]));
    assert.deepStrictEqual(inline.map((m) => m[0]), [], f);
    // on...= attributes are inline event handlers for the browser (CSP violation); the template uses sc-camel-on-*
    const markup = html.replace(/<!--[\s\S]*?-->/g, '').replace(/<script[\s\S]*?<\/script>/g, '');
    assert.deepStrictEqual(markup.match(/\son[a-z]+\s*=/gi) || [], [], f + ': inline event handler attribute');
    for (const m of html.matchAll(/<script src="\.\/([^"]+)"/g)) assert.ok(fs.existsSync(path.join(ROOT, m[1])), m[1]);
  }
});

test('everything except photos and icons is revalidated on every load (a deploy reaches people at once)', () => {
  const caddy = read('deploy/Caddyfile');
  assert.match(caddy, /@fresh not path \/images\/\* \/icons\/\*/);
  assert.match(caddy, /header @fresh Cache-Control "no-cache"/);
});

test('containers: web runs as non-root without capabilities; collector has init and no ports; images pinned', () => {
  const compose = read('deploy/docker-compose.yml');
  const web = compose.slice(compose.indexOf('\n  web:'), compose.indexOf('\nvolumes:'));
  const col = compose.slice(compose.indexOf('\n  collector:'), compose.indexOf('\n  web:'));
  assert.match(web, /user: "10002:10002"/);
  assert.match(web, /cap_drop: \[ALL\]/);
  assert.ok(!/cap_add/.test(web), 'no capability added back');
  assert.ok(!/sysctls|ip_unprivileged_port_start/.test(web), 'no sysctl: the web port is not privileged');
  assert.match(web, /read_only: true/);
  assert.match(web, /no-new-privileges:true/);
  assert.match(web, /healthcheck:/);
  assert.match(web, /container_name: etihad-narx-web/);
  assert.match(col, /container_name: etihad-narx-collector/);
  assert.match(web, /SUPABASE_URL: \$\{SUPABASE_URL/, 'the CSP needs SUPABASE_URL at run time');
  assert.match(col, /init: true/);
  assert.ok(!/^\s+ports:/m.test(col), 'the collector publishes no port');
  assert.match(col, /read_only: true/);
  const df = read('deploy/web.Dockerfile');
  assert.match(df, /^FROM caddy:\d+\.\d+\.\d+-alpine@sha256:[0-9a-f]{64}$/m);
  assert.match(df, /^USER web$/m);
  assert.ok(!/COPY data/.test(df), 'no data folder in the image');
  assert.match(read('deploy/backup.sh'), /PG_IMAGE="postgres:17\.\d+-alpine@sha256:[0-9a-f]{64}"/);
});

test('web is reachable only through nginx: loopback port, plain HTTP inside, TLS and nothing else in nginx', () => {
  const compose = read('deploy/docker-compose.yml');
  const ports = [...compose.matchAll(/^\s+ports:\s*\[([^\]]*)\]/gm)].map((m) => m[1]);
  assert.deepStrictEqual(ports, ['"127.0.0.1:${WEB_PORT:-8744}:8080"'], 'one published port, on 127.0.0.1 only');
  const caddy = read('deploy/Caddyfile');
  assert.match(caddy, /^:8080 \{$/m, 'Caddy listens where compose publishes');
  assert.match(caddy, /^\s+auto_https off$/m);
  const nginx = read('deploy/nginx-site.conf');
  assert.match(nginx, /proxy_pass\s+http:\/\/127\.0\.0\.1:__WEB_PORT__;/);
  assert.match(nginx, /server_name __SITE_DOMAIN__;/);
  assert.match(nginx, /location \/\.well-known\/acme-challenge\//, 'certbot renewals');
  assert.match(nginx, /server_tokens off;/);
  assert.ok(!/^\s*add_header/m.test(nginx), 'headers come from Caddy only (no duplicates)');
  assert.match(read('deploy/.env.example'), /^WEB_PORT=\d+$/m);
});

test('scripts use docker through sudo when the user is not in the docker group (shared server)', () => {
  for (const f of ['deploy/deploy.sh', 'deploy/backup.sh', 'deploy/restore.sh']) {
    const sh = read(f);
    assert.match(sh, /DOCKER=\(sudo -n docker\)/, f);
    const bare = sh.split('\n').filter((l) => /^\s*(\S+\s*\|\s*)?docker (run|compose)\b/.test(l));
    assert.deepStrictEqual(bare, [], f + ': every docker run/compose goes through "${DOCKER[@]}"');
  }
});

test('backup: password never on the command line, SSL required, temp file cleaned up', () => {
  const b = read('deploy/backup.sh');
  assert.match(b, /--env-file "\$ENV_FILE"/);
  assert.ok(!/pg_dump[^\n]*\$SUPABASE_DB_URL/.test(b), 'no connection string in argv');
  assert.match(b, /PGSSLMODE=require/);
  assert.match(b, /trap 'rm -f "\$tmp"' EXIT/);
  assert.match(b, /^set -euo pipefail$/m);
  assert.match(b, /^umask 077$/m);
  assert.match(read('deploy/backup.env.example'), /pooler\.supabase\.com/);
  assert.match(b, /pg_dump --data-only --schema=public --schema=auth/);
});

test('restore: triggers off for the whole load, one transaction, stops on the first error, asks first', () => {
  const r = read('deploy/restore.sh');
  const order = ["--single-transaction -v ON_ERROR_STOP=1", "'SET session_replication_role = replica'", '"$empty_public"', '--file -'];
  order.reduce((at, s) => { const i = r.indexOf(s, at); assert.ok(i > at, s + ' in this order'); return i; }, -1);
  assert.match(r, /RESTORE_CONFIRM/);
  assert.match(r, /--env-file "\$env_file"/);
  assert.match(read('supabase/README.md'), /session_replication_role = replica/, 'same rule as the database docs');
});

test('secrets and server state are ignored by git and by docker', () => {
  const gi = read('.gitignore'), di = read('.dockerignore');
  for (const p of ['*.env', '!*.env.example', 'deploy/backups/', 'private/']) assert.ok(gi.split('\n').includes(p), '.gitignore ' + p);
  for (const p of ['**/*.env', 'private', 'deploy/backups']) assert.ok(di.split('\n').includes(p), '.dockerignore ' + p);
  assert.ok(!fs.existsSync(path.join(ROOT, 'data', 'services.js')), 'services data is not a public file');
});
