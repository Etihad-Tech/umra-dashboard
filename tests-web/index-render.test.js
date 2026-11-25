// Smoke test of the dashboard render logic (index.html): five companies, with and without Hikmat data.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const m = /<script type="text\/x-dc" data-dc-script[^>]*>([\s\S]*?)<\/script>/.exec(html);
assert.ok(m, 'dashboard script not found');

function render(umra, state) {
  class DCLogic { constructor() { this.state = state || {}; } setState(s) { Object.assign(this.state, s); } forceUpdate() {} }
  const win = { UMRA: umra };
  const Component = new Function('DCLogic', 'window', m[1] + '\nreturn Component;')(DCLogic, win);
  return new Component().renderVals();
}

const DATES = ['2026-11-18', '2026-11-25', '2026-12-02'];
const ours = {
  id: 'ADDR-7', name: 'Address 7', n: 7, days: 8, tier: 'Lux', dep: 'wed', range: '', hotel: 'Address', cover: '', hq: '',
  bands: [['2026-11-18', '2026-12-02', 1890, 1990, 2190, 2590]],
};
const comp = (co, pkg, p4) => ({
  co, pkg, key: pkg, cat: 'lux', n: 7, hotel: '', svc: null,
  d: Object.fromEntries(DATES.map((d) => [d, [p4, null, null, null, null]])),
});
const base = (extra) => ({
  today: '2026-11-18', etihad: [ours],
  allpk: [comp('S', 'adress-lux', 1750), comp('F', 'HYATT LUXE', 1950)].concat(extra || []),
  svc: { B: {}, S: {}, F: {}, H: {}, T: {} }, asOfText: 'x', loaded: true,
});

test('renders five companies when Hikmat has a matching package', () => {
  const v = render(base([comp('H', 'Elita', 1700)]));
  assert.strictEqual(v.tiles.length, 5);
  // the mini list is ranked by price (cheapest first); a company with no matching package goes last
  assert.deepStrictEqual(v.mini.map((r) => r.tag), ['HIKMAT', 'SAFO', 'ETIHAD', 'MANDARIN', 'SAFAR TRAVEL']);
  assert.strictEqual(v.legend.length, 5);
  assert.strictEqual(v.rows[0].h, '$1,700');
  assert.ok(v.lines.H && v.lines.H.length > 0, 'a chart line for Hikmat');
  assert.strictEqual(v.svcRows[0].cells.length, 5);
  assert.strictEqual(v.groups[3].cols.length, 5);   // 4-person group has one bar per company
});

test('renders fine when Hikmat has no data at all', () => {
  const v = render(base());
  assert.strictEqual(v.tiles.length, 5);
  const h = v.mini.find((r) => r.tag === 'HIKMAT');
  assert.ok(h);
  assert.strictEqual(v.rows[0].h, '—');
  assert.strictEqual(v.lines.H, '');
});

test('Hikmat price shows only for 4 people (the only price it publishes)', () => {
  const v2 = render(base([comp('H', 'Elita', 1700)]), { px: 2 });
  assert.strictEqual(v2.rows[0].h, '—');
  const v4 = render(base([comp('H', 'Elita', 1700)]), { px: 4 });
  assert.strictEqual(v4.rows[0].h, '$1,700');
});

// Regression: the company list used to be sorted in place (by price) for the tooltip and tiles, so the services table
// put ETIHAD's cells under the MANDARIN header. Prices here are deliberately NOT in column order (F > B > S > H).
test('services table keeps one column per company, in header order, whatever the prices are', () => {
  const svc = (co) => ({ air: co + '-AIR', train: false, xfer: co + '-XFER', hot: [['Makka', co + '-HOTEL', 5]] });
  const u = base([comp('H', 'Elita', 1700)]);
  u.svc = { B: { 'ADDR-7': svc('B') }, S: {}, F: {}, H: {}, T: {} };
  const v = render(u, { px: 4 });
  assert.deepStrictEqual(v.tiles.map((t) => t.tag), ['MANDARIN', 'ETIHAD', 'SAFO', 'HIKMAT', 'SAFAR TRAVEL']);   // tiles ARE by price
  assert.deepStrictEqual(v.mini.map((r) => r.tag), ['HIKMAT', 'SAFO', 'ETIHAD', 'MANDARIN', 'SAFAR TRAVEL']);    // mini list is ranked, columns are not
  const transfer = v.svcRows.find((r) => r.label === 'Transfer');
  assert.deepStrictEqual(transfer.cells.map((c) => c.t1), ['B-XFER', '—', '—', '—', '—']);
  const names = v.mini.map((r) => r.tag);
  assert.strictEqual(names.length, 5);
});

test('the company list is never changed by a render (sorting works on a copy)', () => {
  const src = m[1];
  assert.ok(!/\bCO\.sort\(/.test(src), 'CO.sort( sorts the shared list in place; use [...CO].sort(');
});
