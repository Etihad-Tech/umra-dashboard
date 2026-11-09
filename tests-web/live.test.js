const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const live = require('../live.js');

const ROOT = path.join(__dirname, '..');
const TODAY = '2026-11-09';

// ---- tiny helpers to build database rows
let depId = 0;
function pkg(id, company, key, extra) {
  return Object.assign({ id, company_id: company, external_key: key, name: key.toUpperCase(), tier: 'lux', nights: 9,
                         hotels: [], services: {}, active: true }, extra || {});
}
function weekly(packageId, first, count, prices, extra) {
  const deps = [], pr = [];
  for (let i = 0; i < count; i++) {
    const d = new Date(Date.parse(first + 'T00:00:00Z') + i * 7 * 864e5).toISOString().slice(0, 10);
    const id = ++depId;
    deps.push(Object.assign({ id, package_id: packageId, date: d, sold_out: false, seats_left: null, active: true }, extra || {}));
    [4, 3, 2, 1].forEach((pax, k) => { if (prices[k] != null) pr.push({ departure_id: id, pax, price_usd: prices[k] }); });
  }
  return { deps, pr };
}

test('Tashkent date and time (UTC+5)', () => {
  assert.strictEqual(live.tashkentToday(Date.parse('2026-11-09T18:59:00Z')), '2026-11-09');
  assert.strictEqual(live.tashkentToday(Date.parse('2026-11-09T19:00:00Z')), '2026-11-10');   // already tomorrow there
  assert.strictEqual(live.tashkentText('2026-11-09T07:10:00Z'), '09.11 12:10');
  assert.strictEqual(live.tashkentText('garbage'), '');
});

test('bands: same prices in consecutive weeks merge, a price change or a gap starts a new band', () => {
  const rows = [['2026-11-12', [1890, 1990, 2290, 3250]], ['2026-11-19', [1890, 1990, 2290, 3250]],
                ['2026-11-26', [1990, 2090, 2390, 3450]], ['2026-12-10', [1990, 2090, 2390, 3450]]]
    .map(([date, p]) => ({ date, p }));
  assert.deepStrictEqual(live.buildBands(rows), [
    ['2026-11-12', '2026-11-19', 1890, 1990, 2290, 3250],
    ['2026-11-26', '2026-11-26', 1990, 2090, 2390, 3450],
    ['2026-12-10', '2026-12-10', 1990, 2090, 2390, 3450]]);
  assert.deepStrictEqual(live.buildBands([]), []);
});

test('model: our packages, competitors, tiers, sold out, past dates, missing prices', () => {
  const a = weekly(1, '2026-11-05', 4, [1890, 1990, 2290, 3250]);        // first date is in the past
  const b = weekly(2, '2026-11-12', 3, [1790, 1890, null, null], { seats_left: 7 });
  const c = weekly(3, '2026-11-12', 2, [1700, null, null, null], { sold_out: true });
  const d = weekly(4, '2026-11-12', 2, [null, null, null, null]);        // no price at all
  const e = weekly(5, '2026-11-12', 2, [1500, 1600, 1700, null]);
  const m = live.buildModel({
    today: TODAY,
    packages: [pkg(1, 'B', 'swiss10', { tier: 'premium', services: { depday: 'thu' }, hotels: [{ h: 'x', n: 4 }] }),
               pkg(2, 'S', 'lux', { tier: 'lux' }), pkg(3, 'S', 'ekonom', { tier: 'econom' }),
               pkg(4, 'F', 'empty', { tier: 'lux' }), pkg(5, 'F', 'mystery', { tier: null, name: null })],
    departures: [].concat(a.deps, b.deps, c.deps, d.deps, e.deps),
    prices: [].concat(a.pr, b.pr, c.pr, d.pr, e.pr),
    sources: [{ company_id: 'B', enabled: true, last_ok_at: '2026-11-09T07:00:00Z', consecutive_failures: 0 }],
    meta: { swiss10: { name: 'Swissotel 10', cover: 'images/s.webp', hq: 'images/s-hq.webp', hotel: 'Worth 4 · Swiss 5', days: 10 } }
  });
  assert.strictEqual(m.etihad.length, 1);
  const o = m.etihad[0];
  assert.deepStrictEqual([o.id, o.name, o.tier, o.dep, o.n, o.days, o.cover, o.hotel],
                         ['swiss10', 'Swissotel 10', 'Premium', 'thu', 9, 10, 'images/s.webp', 'Worth 4 · Swiss 5']);
  assert.deepStrictEqual(o.bands, [['2026-11-12', '2026-11-26', 1890, 1990, 2290, 3250]]);   // past 05.11 is gone
  const lux = m.allpk.find((r) => r.key === 'lux');
  assert.deepStrictEqual(lux.d['2026-11-12'], [1790, 1890, null, null, 7]);
  assert.strictEqual(lux.cat, 'lux');
  assert.ok(!m.allpk.find((r) => r.key === 'ekonom'), 'sold out departures are not offered for comparison');
  assert.ok(!m.allpk.find((r) => r.key === 'empty'), 'a package with no price is not shown');
  const mystery = m.allpk.find((r) => r.key === 'mystery');
  assert.strictEqual(mystery.cat, null);
  assert.strictEqual(mystery.pkg, 'mystery');                     // falls back to the external key
  assert.strictEqual(m.stats.unmappedPackages, 1);
  assert.strictEqual(m.today, TODAY);
});

test('our package without any dated price is left out; without meta we still get a usable card', () => {
  const x = weekly(1, '2026-11-12', 2, [1500, 1600, 1700, 2000]);
  const m = live.buildModel({ today: TODAY, packages: [pkg(1, 'B', 'newone', { tier: 'comfort', nights: 7, hotels: [{ name: 'Hilton', n: 3 }] }),
                                                        pkg(9, 'B', 'nodates')],
                              departures: x.deps, prices: x.pr, sources: [], meta: {} });
  assert.deepStrictEqual(m.etihad.map((p) => p.id), ['newone']);
  const o = m.etihad[0];
  assert.deepStrictEqual([o.tier, o.days, o.hotel, o.dep], ['Comfort', 8, 'Hilton 3 kecha', 'thu']);   // 12.11.2026 is a Thursday
});

test('freshness: the OLDEST source decides; a source that never ran, or is down, is visible', () => {
  const base = { today: TODAY, packages: [], departures: [], prices: [], meta: {} };
  let m = live.buildModel(Object.assign({}, base, { sources: [
    { company_id: 'B', last_ok_at: '2026-11-09T07:10:00Z', consecutive_failures: 0 },
    { company_id: 'S', last_ok_at: '2026-11-08T20:00:00Z', consecutive_failures: 0 }] }));
  assert.strictEqual(m.asOf, '2026-11-08T20:00:00Z');
  assert.strictEqual(m.asOfText, '09.11 01:00');
  m = live.buildModel(Object.assign({}, base, { sources: [
    { company_id: 'B', last_ok_at: '2026-11-09T07:10:00Z' }, { company_id: 'F', last_ok_at: null }] }));
  assert.strictEqual(m.asOf, null);
  m = live.buildModel(Object.assign({}, base, { sources: [
    { company_id: 'S', last_ok_at: '2026-11-09T07:10:00Z', consecutive_failures: 3 },
    { company_id: 'F', enabled: false, last_ok_at: null }] }));
  assert.deepStrictEqual(m.stats.down, ['SAFO']);
  assert.ok(m.asOfText.endsWith('SAFO ishlamayapti'));
  assert.ok(m.asOf, 'a disabled source does not make the data look unknown');
});

// ---- reading from Supabase
function fakeClient(tables) {
  const calls = [];
  return {
    calls,
    from(table) {
      const q = { filters: [], orders: [] };
      const chain = {
        select() { return chain; },
        eq(c, v) { q.filters.push(['eq', c, v]); return chain; },
        gte(c, v) { q.filters.push(['gte', c, v]); return chain; },
        order(c) { q.orders.push(c); return chain; },
        range(a, b) {
          calls.push({ table, filters: q.filters, orders: q.orders, a, b });
          const rows = tables[table];
          if (rows instanceof Error) return Promise.resolve({ error: { message: rows.message } });
          return Promise.resolve({ data: rows.slice(a, b + 1), error: null });
        }
      };
      return chain;
    }
  };
}

test('fetchAll reads every page (more than 1000 rows)', async () => {
  const rows = Array.from({ length: 2300 }, (_, i) => ({ id: i }));
  const c = fakeClient({ t: rows });
  const got = await live.fetchAll(c, 't', 'id', ['id']);
  assert.strictEqual(got.length, 2300);
  assert.deepStrictEqual(c.calls.map((x) => [x.a, x.b]), [[0, 999], [1000, 1999], [2000, 2999]]);
  assert.ok(c.calls.every((x) => x.orders.join() === 'id'), 'stable order, otherwise pages could overlap');
});

test('load asks only for active rows and future dates, and fails loudly on a database error', async () => {
  const c = fakeClient({ packages: [], departures: [], price_current: [], sources: [] });
  const m = await live.load(c, {}, Date.parse('2026-11-09T19:30:00Z'));
  assert.strictEqual(m.today, '2026-11-10');
  const dep = c.calls.find((x) => x.table === 'departures');
  assert.deepStrictEqual(dep.filters, [['eq', 'active', true], ['gte', 'date', '2026-11-10']]);
  assert.deepStrictEqual(c.calls.find((x) => x.table === 'packages').filters, [['eq', 'active', true]]);
  const bad = fakeClient({ packages: [], departures: new Error('boom'), price_current: [], sources: [] });
  await assert.rejects(() => live.load(bad, {}), /departures: boom/);
});

// ---- the real page code must accept what live.js produces
function loadComponent(U) {
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const code = /<script type="text\/x-dc" data-dc-script[^>]*>([\s\S]*?)<\/script>/.exec(html)[1];
  const win = { UMRA: U };
  class DCLogic { constructor() { this.state = {}; this.refreshed = 0; } setState(o) { Object.assign(this.state, o); } forceUpdate() { this.refreshed++; } }
  const Component = new Function('DCLogic', 'window', code + '\nreturn Component;')(DCLogic, win);
  const inst = new Component();
  return { inst, win };
}

test('page: draws our package against two competitors from live data', () => {
  const a = weekly(1, '2026-11-12', 4, [1890, 1990, 2290, 3250]);
  const b = weekly(2, '2026-11-12', 4, [2100, 2200, 2400, null]);
  const c = weekly(3, '2026-11-12', 4, [2300, 2400, 2600, null]);
  const U = live.buildModel({
    today: TODAY,
    packages: [pkg(1, 'B', 'swiss10', { tier: 'premium', services: { depday: 'thu' } }),
               pkg(2, 'S', 'prem-s', { tier: 'premium', name: 'Safo Premium' }),
               pkg(3, 'F', 'prem-f', { tier: 'premium', name: 'HYATT-09' })],
    departures: [].concat(a.deps, b.deps, c.deps), prices: [].concat(a.pr, b.pr, c.pr),
    sources: [{ company_id: 'B', last_ok_at: new Date().toISOString(), consecutive_failures: 0 }],
    meta: { swiss10: { name: 'Swissotel 10' } } });
  U.loaded = true;
  U.svc = { B: {}, S: {}, F: {} };
  const { inst, win } = loadComponent(U);
  const v = inst.renderVals();
  assert.strictEqual(typeof win.__umraRefresh, 'function');
  win.__umraRefresh();
  assert.strictEqual(inst.refreshed, 1, 'new data makes the page draw again');
  assert.strictEqual(v.cards.length, 1);
  assert.strictEqual(v.cards[0].name, 'Swissotel 10');
  assert.strictEqual(v.dts.length, 4);
  assert.strictEqual(v.groups.length, 4);
  assert.match(v.verdict.title, /eng arzon/);                       // $1890 vs $2100 and $2300
  assert.strictEqual(v.rows[0].b, '$1,890');
  assert.strictEqual(v.rows[0].s, '$2,100');
  assert.strictEqual(v.rows[0].f, '$2,300');
  assert.ok(v.legend.some((l) => /HYATT-09/.test(l.name)));
  assert.match(v.asOfText, /· yangi$/);
});

test('page: empty, loading and error states do not crash and say so', () => {
  const states = [[{ loaded: false }, /Yuklanmoqda/], [{ loaded: true, loadError: true }, /Yuklab bo'lmadi/],
                  [{ loaded: true, asOf: null, asOfText: "ma'lumot yo'q", etihad: [], allpk: [] }, /ma'lumot yo'q/]];
  states.forEach(([U, re]) => {
    const { inst } = loadComponent(Object.assign({ svc: { B: {}, S: {}, F: {} } }, U));
    const v = inst.renderVals();
    assert.match(v.asOfText, re);
    assert.deepStrictEqual(v.cards, []);
  });
});

test('page: uses the real Tashkent date, not a frozen one', () => {
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  assert.ok(!/const TODAY = '20/.test(html));
});

// ---- nothing old and public is left behind
test('index.html and the service worker only reference files that exist; old price data is gone', () => {
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const sw = fs.readFileSync(path.join(ROOT, 'sw.js'), 'utf8');
  const refs = [...html.matchAll(/<script src="\.\/([^"]+)"/g)].map((m) => m[1])
    .concat([...sw.matchAll(/'\.\/([^']+\.(?:js|html|png|webmanifest))'/g)].map((m) => m[1]));
  assert.ok(refs.length > 8);
  refs.forEach((f) => assert.ok(fs.existsSync(path.join(ROOT, f)), f + ' is referenced but missing'));
  ['competitors.js', 'prices.js', 'events.js', 'info.js', 'packages.js', 'services.js'].forEach((f) =>
    assert.ok(!fs.existsSync(path.join(ROOT, 'data', f)), 'data/' + f + ' must be deleted'));
  assert.ok(!/data\/(competitors|prices|events|info|packages|services)\.js/.test(html + sw));
  assert.ok(refs.includes('sw-register.js'), 'the service worker is registered from a file (no inline script)');
});

test('the page stays hidden behind a loading note until live.js has answered (success or failure)', () => {
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const src = fs.readFileSync(path.join(ROOT, 'live.js'), 'utf8');
  assert.match(html, /html:not\(\.umra-loaded\) main\{display:none/);
  assert.match(html, /id="umra-loading"/);
  assert.ok(src.indexOf("classList.add('umra-loaded')") > 0);
  assert.ok(src.indexOf('refresh();') < src.lastIndexOf('refresh();'), 'refresh runs in both the success and the error branch');
});

test('a competitor that does not publish nights (Safo) is still compared; its nights come from the day label when there is one', () => {
  const a = weekly(1, '2026-11-12', 2, [1890, 1990, 2290, 3250]);
  const b = weekly(2, '2026-11-12', 2, [2000, 2100, 2300, null]);
  const c = weekly(3, '2026-11-12', 2, [2050, 2150, 2350, null]);
  const U = live.buildModel({
    today: TODAY,
    packages: [pkg(1, 'B', 'address10', { tier: 'lux', services: { depday: 'thu' } }),
               pkg(2, 'S', 'adress-lux', { tier: 'lux', nights: null, services: { days_label: '10 kun / 9 kecha' } }),
               pkg(3, 'S', 'xoji-aka-lux', { tier: 'lux', nights: null, services: {} })],
    departures: [].concat(a.deps, b.deps, c.deps), prices: [].concat(a.pr, b.pr, c.pr),
    sources: [{ company_id: 'S', last_ok_at: new Date().toISOString() }], meta: {} });
  assert.deepStrictEqual(U.allpk.map((r) => [r.key, r.n]), [['adress-lux', 9], ['xoji-aka-lux', 0]]);
  U.loaded = true; U.svc = { B: {}, S: {}, F: {} };
  const { inst } = loadComponent(U);
  const v = inst.renderVals();
  assert.strictEqual(v.rows[0].s, '$2,000');                    // the known 9 nights wins over the unknown one
  const none = (() => { const U2 = Object.assign({}, U, { allpk: U.allpk.filter((r) => r.key === 'xoji-aka-lux') });
                        return loadComponent(U2).inst.renderVals(); })();
  assert.strictEqual(none.rows[0].s, '$2,050');                 // unknown nights: still compared, no "0 kecha" shown
  assert.ok(!JSON.stringify(none.mini).includes('0 kecha'));
});

test('liveSvc: Mandarin hotels, airline and trains become the services table data; Safo (no data) gives null', () => {
  const L = require('../live.js');
  const s = L.liveSvc({ hotels: [{ city: 'Makkah', name: 'Hotel A', nights: 5, meal_type: 'BB' }, { city: 'Medina', name: 'Hotel B', nights: 3, meal_type: 'HB' }, { city: '?', name: 'X' }],
    services: { airline: 'HY', route_go: 'TAS-JED', route_ret: 'MED-TAS', trains: ['x'] } });
  assert.deepEqual(s.hot, [['Makka', 'Hotel A', 5, 1], ['Madina', 'Hotel B', 3, 2]]);
  assert.equal(s.air, 'HY · TAS-JED / MED-TAS');
  assert.equal(s.train, 1);
  assert.equal(L.liveSvc({ hotels: [], services: { days_label: '9 kecha' } }), null);
  assert.equal(L.liveSvc({}), null);
});

// ---- review fixes: manual badge, services from Supabase, card texts
test('manual prices are flagged per date and pax (dashboard shows "qo\'lda kiritilgan")', () => {
  const a = weekly(1, '2026-11-12', 2, [1890, 1990, 2290, 3250]);
  const b = weekly(2, '2026-11-12', 2, [2100, 2200, 2400, null]);
  a.pr.find((r) => r.departure_id === a.deps[1].id && r.pax === 4).is_manual = true;
  b.pr.find((r) => r.departure_id === b.deps[0].id && r.pax === 3).is_manual = true;
  const m = live.buildModel({ today: TODAY, packages: [pkg(1, 'B', 'swiss10', { tier: 'premium' }), pkg(2, 'S', 'prem', { tier: 'premium' })],
                              departures: [].concat(a.deps, b.deps), prices: [].concat(a.pr, b.pr), sources: [], meta: {} });
  assert.deepStrictEqual(m.etihad[0].man, { '2026-11-19': { 4: true } });
  assert.deepStrictEqual(m.allpk[0].man, { '2026-11-12': { 3: true } });
  assert.deepStrictEqual(m.svc, { B: {}, S: {}, F: {} }, 'no services row -> empty tables, not an error');
});

test('load: price_current is read with is_manual; services come from app_settings and are optional', async () => {
  const svc = { B: { swiss10: { air: 'TAS → MED' } }, S: { 'Safo Premium': { meal: 2 } }, X: { junk: 1 } };
  const c = fakeClient({ packages: [], departures: [], price_current: [], sources: [],
                         app_settings: [{ key: live.SERVICES_KEY, value: svc }] });
  const m = await live.load(c, {}, Date.parse('2026-11-09T10:00:00Z'));
  assert.deepStrictEqual(m.svc, { B: svc.B, S: svc.S, F: {} });
  const ask = c.calls.find((x) => x.table === 'app_settings');
  assert.deepStrictEqual(ask.filters, [['eq', 'key', 'package_services']]);
  const src = fs.readFileSync(path.join(ROOT, 'live.js'), 'utf8');
  assert.match(src, /'price_current', 'departure_id,pax,price_usd,is_manual'/);
  const denied = fakeClient({ packages: [], departures: [], price_current: [], sources: [], app_settings: new Error('permission denied') });
  assert.deepStrictEqual((await live.load(denied, {})).svc, { B: {}, S: {}, F: {} }, 'an unreadable services row does not break the page');
});

test('page: manual values carry the "qo\'lda kiritilgan" badge in cards, tiles, the date table and the bar hints', () => {
  const a = weekly(1, '2026-11-12', 2, [1890, 1990, 2290, 3250]);
  const b = weekly(2, '2026-11-12', 2, [2100, 2200, 2400, null]);
  a.pr.forEach((r) => { if (r.departure_id === a.deps[0].id && r.pax === 4) r.is_manual = true; });
  b.pr.forEach((r) => { if (r.departure_id === b.deps[0].id && r.pax === 4) r.is_manual = true; });
  const U = live.buildModel({ today: TODAY, packages: [pkg(1, 'B', 'swiss10', { tier: 'premium' }), pkg(2, 'S', 'prem', { tier: 'premium' })],
                              departures: [].concat(a.deps, b.deps), prices: [].concat(a.pr, b.pr), sources: [], meta: {} });
  U.loaded = true;
  const v = loadComponent(U).inst.renderVals();
  assert.strictEqual(v.rows[0].mB, true); assert.strictEqual(v.rows[0].mS, true);
  assert.strictEqual(v.rows[1].mB, false, 'the next week is automatic');
  assert.strictEqual(v.cards[0].manual, true);
  assert.ok(v.tiles.find((t) => t.tag === 'ETIHAD').manual);
  assert.match(v.groups[3].cols.find((c) => /^ETIHAD/.test(c.hint)).hint, /qo'lda kiritilgan/);
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  assert.ok((html.match(/qo'lda kiritilgan<\/span><\/sc-if>/g) || []).length >= 5, 'badge markup in the template');
});

test('page: a card falls back to the price that exists; no "null kecha"; the real departure weekday', () => {
  const a = weekly(1, '2026-11-16', 2, [null, null, 2290, 3250]);        // Mondays, only 2- and 1-person prices
  const U = live.buildModel({ today: TODAY, packages: [pkg(1, 'B', 'mon7', { tier: 'lux', nights: null })],
                              departures: a.deps, prices: a.pr, sources: [], meta: {} });
  U.loaded = true;
  const v = loadComponent(U).inst.renderVals();
  assert.strictEqual(v.cards[0].price, '$2,290 · 2 kishi');
  assert.strictEqual(v.cards[0].line, 'dushanba');
  assert.ok(!/null|undefined/.test(JSON.stringify([v.cards, v.bannerLine, v.ourLine, v.mini])), 'no null/undefined text');
  assert.strictEqual(v.bannerLine, 'Lux · dushanba');
  assert.strictEqual(live.normDay('Thursday'), 'thu');
  assert.strictEqual(live.normDay('payshanba'), '');
});

test('page: our services come from the imported table, else from what the ETIHAD source sent', () => {
  const a = weekly(1, '2026-11-12', 1, [1890, 1990, 2290, 3250]);
  const U = live.buildModel({ today: TODAY, sources: [], meta: {}, departures: a.deps, prices: a.pr,
    packages: [pkg(1, 'B', 'swiss10', { tier: 'premium', hotels: [{ city: 'Makkah', name: 'Swissotel', nights: 5, meal_type: 'BB' }] })] });
  U.loaded = true;
  const hotelText = (V) => V.svcRows.find((r) => r.label === 'Makka hotel').cells[0].t1;
  assert.strictEqual(hotelText(loadComponent(U).inst.renderVals()), 'Swissotel');
  U.svc = { B: { swiss10: { air: 'TAS → MED', hot: [['Makka', 'Swissotel Makkah', 5, 1]], train: 1 } }, S: {}, F: {} };
  assert.strictEqual(hotelText(loadComponent(U).inst.renderVals()), 'Swissotel Makkah');
});
