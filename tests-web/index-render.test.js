// Smoke test of the dashboard render logic (index.html): four companies, with and without Hikmat data.
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
  svc: { B: {}, S: {}, F: {}, H: {} }, asOfText: 'x', loaded: true,
});

test('renders four companies when Hikmat has a matching package', () => {
  const v = render(base([comp('H', 'Elita', 1700)]));
  assert.strictEqual(v.tiles.length, 4);
  assert.deepStrictEqual(v.mini.map((r) => r.tag).sort(), ['ETIHAD', 'HIKMAT', 'MANDARIN', 'SAFO']);
  assert.strictEqual(v.legend.length, 4);
  assert.strictEqual(v.rows[0].h, '$1,700');
  assert.ok(v.lines.H && v.lines.H.length > 0, 'a chart line for Hikmat');
  assert.strictEqual(v.svcRows[0].cells.length, 4);
  assert.strictEqual(v.groups[3].cols.length, 4);   // 4-person group has four bars
});

test('renders fine when Hikmat has no data at all', () => {
  const v = render(base());
  assert.strictEqual(v.tiles.length, 4);
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
