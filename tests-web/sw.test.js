const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function loadSw() {
  const handlers = {}, store = {};
  const self = {
    location: { origin: 'https://site.example' },
    addEventListener: (n, f) => { handlers[n] = f; },
    skipWaiting: () => {}, clients: { claim: () => {} },
  };
  const caches = {
    open: async () => ({ put: async (req) => { store[req.url] = true; }, addAll: async () => {} }),
    match: async () => undefined, keys: async () => [], delete: async () => true,
  };
  const ctx = vm.createContext({ self, caches, URL, fetch: async () => ({ ok: true, type: 'basic', clone() { return this; } }) });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'sw.js'), 'utf8'), ctx);
  return { handlers, store };
}
function fetchEvent(url, method = 'GET') {
  const e = { request: { url, method }, responded: false, respondWith(p) { e.responded = true; e.promise = p; } };
  return e;
}

test('Supabase API requests are never handled or cached by the service worker', () => {
  const { handlers } = loadSw();
  const e = fetchEvent('https://abc.supabase.co/rest/v1/departures?select=*');
  handlers.fetch(e);
  assert.equal(e.responded, false);
});

test('other third-party origins are left alone', () => {
  const { handlers } = loadSw();
  const e = fetchEvent('https://evil.example/x.js'); handlers.fetch(e);
  assert.equal(e.responded, false);
});

test('own files are served network-first and cached', async () => {
  const { handlers, store } = loadSw();
  const e = fetchEvent('https://site.example/index.html'); handlers.fetch(e);
  assert.equal(e.responded, true);
  await e.promise; await new Promise((r) => setTimeout(r, 0));
  assert.ok(store['https://site.example/index.html']);
});

test('fonts are still cached', () => {
  const { handlers } = loadSw();
  const e = fetchEvent('https://fonts.gstatic.com/s/inter.woff2'); handlers.fetch(e);
  assert.equal(e.responded, true);
});

test('POST is ignored', () => {
  const { handlers } = loadSw();
  const e = fetchEvent('https://site.example/x', 'POST'); handlers.fetch(e);
  assert.equal(e.responded, false);
});

test('new files are in the precache list and the cache version was bumped', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'sw.js'), 'utf8');
  for (const f of ['./auth.js', './config.js', './vendor/supabase.js', './sw-register.js']) assert.ok(src.includes(f), f);
  assert.ok(!src.includes('./data/'), 'no data files in the app shell');
  const v = /VERSION = 'umra-v(\d+)'/.exec(src);
  assert.ok(v && +v[1] >= 7, 'VERSION bumped for this release');
});

test('install precaches the app shell past the HTTP cache (cache: reload), so it never stores an old copy', async () => {
  let added = null, waited = null;
  const handlers = {};
  const self = { location: { origin: 'https://site.example' }, addEventListener: (n, f) => { handlers[n] = f; },
                 skipWaiting: () => {}, clients: { claim: () => {} } };
  class Request { constructor(url, init) { this.url = url; this.cache = init && init.cache; } }
  const caches = { open: async () => ({ addAll: async (reqs) => { added = reqs; } }) };
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'sw.js'), 'utf8'), vm.createContext({ self, caches, URL, Request }));
  handlers.install({ waitUntil: (p) => { waited = p; } });
  await waited;
  assert.ok(added.length > 10);
  assert.ok(added.every((r) => r instanceof Request && r.cache === 'reload'));
});
