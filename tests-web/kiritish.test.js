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
const chip = async (p, box, id) => { p.doc.querySelector('#' + box + ' [data-id="' + id + '"]').click(); await settle(); };
const cell = (p, dep, pax) => p.doc.getElementById('c-' + dep + '-' + pax);
const type = (p, el, v) => { el.value = v; el.dispatchEvent(new p.win.Event('input')); };
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
  await chip(p, 'p-companies', 'F');
  assert.equal(p.doc.querySelectorAll('img').length, 0);
  assert.match(p.doc.getElementById('p-packages').textContent, /<img/);
});

// ---- price table (Phase 6b)
const DATA3 = () => {
  const d = DATA();
  d.departures = [
    { id: 10, package_id: 1, date: '2026-11-01', sold_out: false, is_manual: false, active: true },   // already past (today = 08.11)
    { id: 11, package_id: 1, date: '2026-11-12', sold_out: true, is_manual: false, active: true },
    { id: 12, package_id: 1, date: '2026-11-19', sold_out: false, is_manual: false, active: true },
    { id: 13, package_id: 1, date: '2026-11-26', sold_out: false, is_manual: false, active: true },
  ];
  d.price_current = [{ departure_id: 12, pax: 4, price_usd: 1190, is_manual: false, conflict: false },
                     { departure_id: 12, pax: 2, price_usd: 1390, is_manual: true, conflict: false },
                     { departure_id: 13, pax: 4, price_usd: 1190, is_manual: false, conflict: true }];
  return d;
};
async function open3(opts) {
  const p = await page(DATA3(), opts);
  await chip(p, 'p-companies', 'F'); await chip(p, 'p-packages', '1');
  return p;
}

test('price table: company -> package shows every upcoming date as a row with the CURRENT prices in the cells', async () => {
  const p = await open3();
  assert.equal(p.doc.querySelectorAll('#p-table tbody tr').length, 3, 'past date is not shown');
  assert.equal(cell(p, 12, 4).value, '1190');
  assert.equal(cell(p, 12, 2).value, '1390');
  assert.equal(cell(p, 12, 3).value, '', 'no price = empty cell');
  assert.equal(p.doc.getElementById('s-11').checked, true, 'sold-out date is ticked');
  assert.match(p.doc.querySelector('#p-table tbody tr:nth-child(3) .date').textContent, /⚠/, 'conflict is flagged');
  assert.equal(p.doc.getElementById('p-save').disabled, true, 'nothing changed yet');
  assert.equal(text(p, 'p-count'), "O'zgarish yo'q");
});

test('price table: editing a cell marks it, counts it, and saving writes only what changed', async () => {
  const p = await open3();
  type(p, cell(p, 12, 4), '1 090');
  type(p, cell(p, 12, 3), '1000');
  assert.equal(cell(p, 12, 4).className, 'changed');
  assert.equal(cell(p, 12, 2).className, 'manual', 'untouched manual price keeps its mark');
  assert.match(text(p, 'p-count'), /2 ta o'zgarish/);
  assert.equal(p.doc.getElementById('p-save').disabled, false);
  submit(p, '#form-price'); await settle();
  assert.equal(p.client.writes.length, 1);
  const w = p.client.writes[0];
  assert.equal(w.table, 'manual_entries');
  assert.deepEqual(w.payload.map((r) => [r.departure_id, r.pax, r.price_usd]).sort(), [[12, 3, 1000], [12, 4, 1090]]);
  assert.equal(w.payload[0].valid_until, '2026-11-15T00:00:00.000Z', 'default 7 days');
  assert.match(p.doc.querySelector('#form-price .status').textContent, /Saqlandi.*2 ta o'zgarish/);
});

test('price table: retyping the same price is not a change; an empty cell means unchanged', async () => {
  const p = await open3();
  type(p, cell(p, 12, 4), '1190');
  type(p, cell(p, 12, 2), '');
  assert.equal(text(p, 'p-count'), "O'zgarish yo'q");
  submit(p, '#form-price'); await settle();
  assert.equal(p.client.writes.length, 0);
  assert.match(p.doc.querySelector('#form-price .status').textContent, /Kamida bitta/);
});

test('price table: junk blocks saving; a big deviation asks first and "no" writes nothing', async () => {
  const p = await open3({ confirm: false });
  type(p, cell(p, 12, 4), 'abc');
  assert.match(text(p, 'p-count'), /butun son/);
  submit(p, '#form-price'); await settle();
  assert.equal(p.client.writes.length, 0);
  type(p, cell(p, 12, 4), '2500');
  submit(p, '#form-price'); await settle();
  assert.equal(p.confirms.length, 1);
  assert.match(p.confirms[0], /1 ta narx/);
  assert.equal(p.client.writes.length, 0);
});

test('price table: ticking "Tugagan" writes a sold-out row; the validity selector sets valid_until', async () => {
  const p = await open3();
  p.doc.getElementById('s-12').checked = true; p.doc.getElementById('s-12').dispatchEvent(new p.win.Event('change'));
  p.doc.getElementById('s-11').checked = false; p.doc.getElementById('s-11').dispatchEvent(new p.win.Event('change'));
  const days = p.doc.getElementById('p-days'); days.value = '14'; days.dispatchEvent(new p.win.Event('change'));
  submit(p, '#form-price'); await settle();
  const rows = p.client.writes[0].payload;
  assert.deepEqual(rows.map((r) => [r.departure_id, r.sold_out]).sort(), [[11, false], [12, true]]);
  assert.equal(rows[0].valid_until, '2026-11-22T00:00:00.000Z');
});

test('price table: one price for all dates fills the cells (sold-out rows skipped) and saves nothing until "Saqlash"', async () => {
  const p = await open3();
  type(p, p.doc.getElementById('b-pax4'), '1200');
  p.doc.getElementById('b-apply').click();
  assert.equal(p.client.writes.length, 0);
  assert.equal(cell(p, 12, 4).value, '1200'); assert.equal(cell(p, 13, 4).value, '1200');
  assert.equal(cell(p, 11, 4).value, '', 'sold-out date is skipped');
  assert.match(text(p, 'p-count'), /2 ta o'zgarish/);
  submit(p, '#form-price'); await settle();
  assert.deepEqual(p.client.writes[0].payload.map((r) => r.departure_id).sort(), [12, 13]);
});

test('price table: database errors become short messages and the button stays usable', async () => {
  const p = await page(DATA3(), { writeError: { code: '42501', message: 'row-level security policy secret detail' } });
  await chip(p, 'p-companies', 'F'); await chip(p, 'p-packages', '1');
  type(p, cell(p, 12, 4), '1100');
  submit(p, '#form-price'); await settle();
  const msg = p.doc.querySelector('#form-price .status').textContent;
  assert.match(msg, /Ruxsat yo'q/);
  assert.ok(!/secret/.test(msg), 'raw database text must not reach the page');
  assert.equal(p.doc.getElementById('p-save').disabled, false);
});

test('price table: the last company and package are remembered and restored (storage is optional)', async () => {
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
  a.doc.querySelector('#p-companies [data-id="F"]').click(); await settle();
  a.doc.querySelector('#p-packages [data-id="1"]').click(); await settle();
  assert.ok(store['umra.kiritish.last']);
  const b = await mk(); await settle();                                  // the next visit opens the same table
  assert.ok(b.doc.querySelector('#p-companies .chip.on[data-id="F"]'));
  assert.ok(b.doc.querySelector('#p-packages .chip.on[data-id="1"]'));
  assert.equal(b.doc.querySelectorAll('#p-table tbody tr').length, 3);
  store['umra.kiritish.last'] = '{broken';                              // broken or stale storage must not break the page
  assert.equal((await mk()).doc.querySelectorAll('#p-table').length, 0);
  store['umra.kiritish.last'] = JSON.stringify({ co: 'ZZ', pkg: '9' });
  assert.equal((await mk()).doc.querySelectorAll('#p-table').length, 0);
});
