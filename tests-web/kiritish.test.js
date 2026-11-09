const test = require('node:test');
const assert = require('node:assert');
const { JSDOM } = require('jsdom');
const K = require('../kiritish.js');
const { settle } = require('./helpers');

/** Fake client: tables = {name: rows}; every write is recorded in `writes`. */
function fakeClient(tables, opts = {}) {
  const writes = [];
  function builder(table) {
    const b = { table, filters: [], mode: 'select', payload: null, single: false };
    const api = {
      select() { return api; },
      eq(c, v) { b.filters.push(['eq', c, v]); return api; },
      is(c, v) { b.filters.push(['is', c, v]); return api; },
      gt(c, v) { b.filters.push(['gt', c, v]); return api; },
      in(c, v) { b.filters.push(['in', c, v]); return api; },
      order() { return api; },
      single() { b.single = true; return api; },
      insert(p) { b.mode = 'insert'; b.payload = p; return api; },
      update(p) { b.mode = 'update'; b.payload = p; return api; },
      then(res, rej) { return run().then(res, rej); },
    };
    async function run() {
      if (b.mode !== 'select') {
        writes.push({ table, mode: b.mode, payload: b.payload, filters: b.filters });
        if (opts.writeError) return { error: opts.writeError };
        return { data: b.single ? { id: 900 } : null, error: null };
      }
      let rows = (tables[table] || []).slice();
      b.filters.forEach(([op, c, v]) => {
        if (op === 'eq') rows = rows.filter((r) => r[c] === v || String(r[c]) === String(v));
        if (op === 'in') rows = rows.filter((r) => v.includes(r[c]));
      });
      return { data: rows, error: null };
    }
    return api;
  }
  return { from: builder, writes };
}

const DATA = () => ({
  companies: [{ id: 'B', name: 'ETIHAD', short: 'ETIHAD' }, { id: 'F', name: 'Family Mandarin', short: 'Family M.' }],
  packages: [
    { id: 1, company_id: 'F', external_key: 'HYATT-09', name: 'Hyatt', tier: 'comfort', departure_city: 'TOSHKENT', is_manual: false, active: true },
    { id: 2, company_id: 'B', external_key: 'ET-1', name: null, tier: 'lux', departure_city: null, is_manual: false, active: true },
  ],
  departures: [{ id: 10, package_id: 1, date: '2026-12-12', sold_out: false, is_manual: false, active: true }],
  price_current: [{ departure_id: 10, pax: 4, price_usd: 1190, is_manual: false, conflict: false }],
  manual_entries: [], package_matches: [], profile_names: [],
});

async function page(tables = DATA(), opts = {}) {
  const dom = new JSDOM('<!doctype html><body><main id="kiritish"></main></body>');
  const doc = dom.window.document;
  const client = fakeClient(tables, opts);
  const confirms = [];
  const k = K.create({ document: doc, client, root: doc.getElementById('kiritish'), now: () => Date.UTC(2026, 10, 8),
    confirm: (m) => { confirms.push(m); return opts.confirm !== false; } });
  await k.start(); await settle();
  return { doc, client, confirms, k, win: dom.window };
}
async function pick(p, id, value) {
  const el = p.doc.getElementById(id);
  el.value = value;
  el.dispatchEvent(new p.win.Event('change'));
  await settle();
}
const submit = (p, sel) => p.doc.querySelector(sel).dispatchEvent(new p.win.Event('submit', { cancelable: true, bubbles: true }));
const text = (p, id) => p.doc.getElementById(id).textContent;

test('parsePrice accepts plain, spaced and $ numbers; rejects junk and zero', () => {
  assert.equal(K.parsePrice('1190'), 1190);
  assert.equal(K.parsePrice(' $1,190 '), 1190);
  assert.equal(K.parsePrice(''), null);
  assert.ok(Number.isNaN(K.parsePrice('12.5')));
  assert.ok(Number.isNaN(K.parsePrice('abc')));
  assert.ok(Number.isNaN(K.parsePrice('0')));
  assert.ok(Number.isNaN(K.parsePrice('-5')));
});

test('buildEntryRows: one row per filled price, a separate sold-out row, valid_until = days ahead', () => {
  const rows = K.buildEntryRows(10, { 1: null, 2: null, 3: 900, 4: 1100 }, true, 'tel', 3, Date.UTC(2026, 10, 8));
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((r) => r.pax), [3, 4, undefined]);
  assert.equal(rows[2].sold_out, true);
  assert.equal(rows[0].valid_until, '2026-11-11T00:00:00.000Z');
  assert.ok(rows.every((r) => r.departure_id === 10 && r.note === 'tel'));
  assert.equal(K.buildEntryRows(10, {}, null, '', 7).length, 0);
});

test('bigDeviations flags only changes over 30%', () => {
  assert.deepEqual(K.bigDeviations({ 4: 1700, 3: 1000 }, { 4: 1190, 3: 1000 }), [4]);
  assert.deepEqual(K.bigDeviations({ 4: 1200 }, { 4: 1190 }), []);
  assert.deepEqual(K.bigDeviations({ 4: 1200 }, {}), []);
});

test('price form: company -> package -> date shows the current price, saving inserts rows', async () => {
  const p = await page();
  await pick(p, 'p-company', 'F'); await pick(p, 'p-package', '1'); await pick(p, 'p-departure', '10');
  assert.match(text(p, 'p-current'), /4 kishi \$1190/);
  p.doc.getElementById('p-pax4').value = '1 250';
  submit(p, '#form-price'); await settle();
  assert.equal(p.client.writes.length, 1);
  const w = p.client.writes[0];
  assert.equal(w.table, 'manual_entries');
  assert.equal(w.payload[0].price_usd, 1250);
  assert.equal(w.payload[0].pax, 4);
  assert.equal(w.payload[0].departure_id, 10);
  assert.equal(w.payload[0].valid_until, '2026-11-15T00:00:00.000Z');
  assert.match(p.doc.querySelector('#form-price .status').textContent, /Saqlandi/);
  assert.equal(p.doc.getElementById('p-pax4').value, '', 'inputs cleared after saving');
});

test('price form: nothing is written without a departure, with junk, or with no values', async () => {
  const p = await page();
  submit(p, '#form-price'); await settle();
  assert.equal(p.client.writes.length, 0);
  await pick(p, 'p-company', 'F'); await pick(p, 'p-package', '1'); await pick(p, 'p-departure', '10');
  submit(p, '#form-price'); await settle();
  assert.match(p.doc.querySelector('#form-price .status').textContent, /Kamida bitta narx/);
  p.doc.getElementById('p-pax4').value = 'abc';
  submit(p, '#form-price'); await settle();
  assert.match(p.doc.querySelector('#form-price .status').textContent, /butun son/);
  assert.equal(p.client.writes.length, 0);
});

test('price form: a big deviation asks first and a "no" writes nothing', async () => {
  const p = await page(DATA(), { confirm: false });
  await pick(p, 'p-company', 'F'); await pick(p, 'p-package', '1'); await pick(p, 'p-departure', '10');
  p.doc.getElementById('p-pax4').value = '2500';
  submit(p, '#form-price'); await settle();
  assert.equal(p.confirms.length, 1);
  assert.equal(p.client.writes.length, 0);
});

test('price form: sold-out only is allowed; database errors become short messages', async () => {
  const p = await page(DATA(), { writeError: { code: '42501', message: 'row-level security policy secret detail' } });
  await pick(p, 'p-company', 'F'); await pick(p, 'p-package', '1'); await pick(p, 'p-departure', '10');
  p.doc.getElementById('p-sold').value = 'yes';
  submit(p, '#form-price'); await settle();
  assert.equal(p.client.writes[0].payload[0].sold_out, true);
  const msg = p.doc.querySelector('#form-price .status').textContent;
  assert.match(msg, /Ruxsat yo'q/);
  assert.ok(!/secret/.test(msg), 'raw database text must not reach the page');
});

test('new package + first date are both marked manual', async () => {
  const p = await page();
  await pick(p, 'a-company', 'F');
  p.doc.getElementById('a-key').value = 'NEW-1';
  p.doc.getElementById('a-nights').value = '10';
  p.doc.getElementById('a-date').value = '2027-01-05';
  submit(p, '#form-add'); await settle();
  const [pk, dp] = p.client.writes;
  assert.equal(pk.table, 'packages'); assert.equal(pk.payload.is_manual, true);
  assert.equal(pk.payload.company_id, 'F'); assert.equal(pk.payload.external_key, 'NEW-1'); assert.equal(pk.payload.nights, 10);
  assert.equal(dp.table, 'departures'); assert.equal(dp.payload.package_id, 900); assert.equal(dp.payload.is_manual, true);
});

test('new date on an existing package creates only a manual departure; bad dates are refused', async () => {
  const p = await page();
  await pick(p, 'a-company', 'F'); await pick(p, 'a-package', '1');
  p.doc.getElementById('a-date').value = '2027-01-05';
  p.doc.getElementById('a-return').value = '2027-01-01';
  submit(p, '#form-add'); await settle();
  assert.equal(p.client.writes.length, 0);
  p.doc.getElementById('a-return').value = '2027-01-15';
  submit(p, '#form-add'); await settle();
  assert.equal(p.client.writes.length, 1);
  assert.deepEqual(p.client.writes[0].payload, { package_id: 1, date: '2027-01-05', return_date: '2027-01-15', is_manual: true });
});

test('duplicate package gives a friendly message', async () => {
  const p = await page(DATA(), { writeError: { code: '23505', message: 'duplicate key' } });
  await pick(p, 'a-company', 'F');
  p.doc.getElementById('a-key').value = 'HYATT-09';
  p.doc.getElementById('a-date').value = '2027-01-05';
  submit(p, '#form-add'); await settle();
  assert.match(p.doc.querySelector('#form-add .status').textContent, /allaqachon/);
});

test('package match inserts our -> their; list can deactivate (never delete)', async () => {
  const t = DATA();
  t.package_matches = [{ id: 5, our_package_id: 2, their_package_id: 1, note: 'bir xil', active: true }];
  const p = await page(t);
  await pick(p, 'm-company', 'F');
  assert.match(p.doc.getElementById('m-list').textContent, /HYATT-09/);
  p.doc.getElementById('m-our').value = '2'; p.doc.getElementById('m-their').value = '1';
  submit(p, '#form-match'); await settle();
  assert.deepEqual(p.client.writes[0].payload, { our_package_id: 2, their_package_id: 1, note: null });
  p.doc.querySelector('#m-list button').click(); await settle();
  const u = p.client.writes[1];
  assert.equal(u.mode, 'update'); assert.deepEqual(u.payload, { active: false });
  assert.ok(!p.client.writes.some((w) => w.mode === 'delete'));
});

test('active manual entries show who/what and a conflict flag; clearing sets cleared_at', async () => {
  const t = DATA();
  t.manual_entries = [{ id: 7, pax: 4, price_usd: 1000, sold_out: null, note: 'tel', created_at: '2026-11-07T10:00:00Z',
    valid_until: '2026-11-14T10:00:00Z', created_by: 'u1', departure_id: 10,
    departures: { date: '2026-12-12', packages: { external_key: 'HYATT-09', company_id: 'F' } } }];
  t.profile_names = [{ user_id: 'u1', display_name: 'Aziz' }];
  t.price_current = [{ departure_id: 10, pax: 4, price_usd: 1000, conflict: true }];
  const p = await page(t);
  p.doc.getElementById('tab-3').click(); await settle();
  const li = p.doc.getElementById('e-list').textContent;
  assert.match(li, /HYATT-09/); assert.match(li, /Aziz/); assert.match(li, /\$1000/); assert.match(li, /Saytda boshqa narx/);
  p.doc.querySelector('#e-list button').click(); await settle();
  const w = p.client.writes[0];
  assert.equal(w.mode, 'update'); assert.equal(w.table, 'manual_entries');
  assert.equal(w.payload.cleared_at, '2026-11-08T00:00:00.000Z');
});

test('page never uses innerHTML for data: markup in names stays text', async () => {
  const t = DATA();
  t.packages[0].name = '<img src=x onerror=alert(1)>';
  const p = await page(t);
  await pick(p, 'p-company', 'F');
  assert.equal(p.doc.querySelectorAll('img').length, 0);
  assert.match(p.doc.getElementById('p-package').textContent, /<img/);
});

// ---- Phase 6: easier price entry
const DATA3 = () => {
  const d = DATA();
  d.departures = [
    { id: 10, package_id: 1, date: '2026-11-01', sold_out: false, is_manual: false, active: true },   // already past (today = 08.11)
    { id: 11, package_id: 1, date: '2026-11-12', sold_out: true, is_manual: false, active: true },
    { id: 12, package_id: 1, date: '2026-11-19', sold_out: false, is_manual: false, active: true },
    { id: 13, package_id: 1, date: '2026-11-26', sold_out: false, is_manual: false, active: true },
  ];
  d.price_current = [{ departure_id: 12, pax: 4, price_usd: 1190, is_manual: false, conflict: false },
                     { departure_id: 12, pax: 2, price_usd: 1390, is_manual: true, conflict: false }];
  return d;
};

test('easier entry: the nearest upcoming date with seats is chosen, current prices become hints', async () => {
  const p = await page(DATA3());
  await pick(p, 'p-company', 'F'); await pick(p, 'p-package', '1');
  assert.equal(p.doc.getElementById('p-departure').value, '12');          // not the past one, not the sold-out one
  assert.match(text(p, 'p-current'), /4 kishi \$1190/);
  assert.equal(p.doc.getElementById('p-pax4').placeholder, '1190');
  assert.equal(p.doc.getElementById('p-pax2').placeholder, '1390');
  assert.equal(p.doc.getElementById('p-pax3').placeholder, '');
  assert.equal(p.doc.getElementById('p-pax4').value, '', 'hints are not values: empty stays "unchanged"');
});

test('easier entry: "all dates" writes the price for every date of the package after one confirmation', async () => {
  const p = await page(DATA3());
  await pick(p, 'p-company', 'F'); await pick(p, 'p-package', '1');
  assert.match(p.doc.getElementById('p-alllabel').textContent, /4 ta/);
  p.doc.getElementById('p-pax4').value = '1200';
  const box = p.doc.getElementById('p-all'); box.checked = true;
  submit(p, '#form-price'); await settle();
  assert.equal(p.confirms.length, 1);
  assert.match(p.confirms[0], /4 ta sanaga/);
  assert.equal(p.client.writes.length, 1);
  assert.deepEqual(p.client.writes[0].payload.map((r) => r.departure_id), [10, 11, 12, 13]);
  assert.ok(p.client.writes[0].payload.every((r) => r.price_usd === 1200 && r.pax === 4));
  assert.equal(box.checked, false, 'the box resets so the next save is not applied to everything by accident');
});

test('easier entry: declining the "all dates" question writes nothing; a single-date package has no such option', async () => {
  const p = await page(DATA3(), { confirm: false });
  await pick(p, 'p-company', 'F'); await pick(p, 'p-package', '1');
  p.doc.getElementById('p-pax4').value = '1200';
  p.doc.getElementById('p-all').checked = true;
  submit(p, '#form-price'); await settle();
  assert.equal(p.client.writes.length, 0);
  const one = await page(DATA());
  await pick(one, 'p-company', 'F'); await pick(one, 'p-package', '1');
  assert.equal(one.doc.getElementById('p-allrow').style.display, 'none');
});

test('easier entry: the last company and package are remembered and restored (storage is optional)', async () => {
  const store = {}; const storage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } };
  const mk = async () => {
    const dom = new JSDOM('<!doctype html><body><main id="kiritish"></main></body>');
    const doc = dom.window.document;
    const k = K.create({ document: doc, client: fakeClient(DATA3()), root: doc.getElementById('kiritish'), storage,
      now: () => Date.UTC(2026, 10, 8), confirm: () => true });
    await k.start(); await settle();
    return { doc, win: dom.window };
  };
  const a = await mk();
  const el = a.doc.getElementById('p-company'); el.value = 'F'; el.dispatchEvent(new a.win.Event('change')); await settle();
  const pk = a.doc.getElementById('p-package'); pk.value = '1'; pk.dispatchEvent(new a.win.Event('change')); await settle();
  assert.ok(store['umra.kiritish.last']);
  const b = await mk(); await settle();                                  // the next visit
  assert.equal(b.doc.getElementById('p-company').value, 'F');
  assert.equal(b.doc.getElementById('p-package').value, '1');
  assert.equal(b.doc.getElementById('p-departure').value, '12');
  store['umra.kiritish.last'] = '{broken';                              // broken or stale storage must not break the page
  const c = await mk();
  assert.equal(c.doc.getElementById('p-company').value, '');
  store['umra.kiritish.last'] = JSON.stringify({ co: 'ZZ', pkg: '9' });
  const d = await mk();
  assert.equal(d.doc.getElementById('p-company').value, '');
});
