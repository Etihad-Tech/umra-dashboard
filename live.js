/* Live data for the dashboard (Phase 6).
 *
 * After the login gate opens, this file reads packages, departures and current prices from Supabase
 * (row level security applies: only an active signed-in user gets rows) and fills window.UMRA with the
 * same structures the old static data/*.js files had:
 *   UMRA.etihad  our packages  [{id,name,n,tier,dep,hotel,cover,hq,bands:[[from,to,p4,p3,p2,p1],...],man}]
 *   UMRA.allpk   competitors   [{co,pkg,cat,n,hotel,d:{date:[p4,p3,p2,p1,seats]},man}]
 *                man = {date: {pax: true}} for prices an editor typed in by hand (shown as "qo'lda kiritilgan")
 *   UMRA.svc     services table data {B:{key:..}, S:{name:..}, F:{name:..}} from app_settings 'package_services'
 *                (imported with collector.import_services; not a public file). Missing -> empty, never an error.
 *   UMRA.asOf / asOfText       when the OLDEST source last ran successfully (stale data must be visible)
 * Then it asks the page to draw again (window.__umraRefresh, set by index.html).
 * Money = integer USD, null = no data. No secrets here: only the public anon key is used (by auth.js).
 */
(function () {
  'use strict';

  var PAGE = 1000;                       // PostgREST returns at most 1000 rows per request
  var REFRESH_MS = 5 * 60 * 1000;
  var TASHKENT_MS = 5 * 3600 * 1000;     // Asia/Tashkent is UTC+5 all year round
  var TIER_LABEL = { premium: 'Premium', lux: 'Lux', comfort: 'Comfort', econom: 'Comfort' };
  var NAMES = { B: 'ETIHAD', S: 'SAFO', F: 'MANDARIN' };
  var DOWN_AFTER = 3;                    // same number as the collector's source_down alert
  var WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
  var SERVICES_KEY = 'package_services';

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function tashkentToday(nowMs) {
    return new Date((nowMs == null ? Date.now() : nowMs) + TASHKENT_MS).toISOString().slice(0, 10);
  }
  function tashkentText(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return '';
    var d = new Date(t + TASHKENT_MS);
    return pad(d.getUTCDate()) + '.' + pad(d.getUTCMonth() + 1) + ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes());
  }
  function dayNo(s) { return Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / 864e5; }
  function weekdayOf(s) { return WEEKDAYS[new Date(dayNo(s) * 864e5).getUTCDay()]; }
  /** "thu", "Thursday", "THU" -> "thu"; anything else -> '' */
  function normDay(x) { var k = String(x || '').trim().toLowerCase().slice(0, 3); return WEEKDAYS.indexOf(k) >= 0 ? k : ''; }
  /** app_settings value -> {B:{},S:{},F:{}} with only plain objects inside */
  function normSvc(v) {
    var out = { B: {}, S: {}, F: {} };
    if (!v || typeof v !== 'object') return out;
    ['B', 'S', 'F'].forEach(function (co) { if (v[co] && typeof v[co] === 'object' && !Array.isArray(v[co])) out[co] = v[co]; });
    return out;
  }

  function hotelsText(hotels) {
    if (!Array.isArray(hotels)) return '';
    return hotels.map(function (h) {
      if (typeof h === 'string') return h;
      if (!h || typeof h !== 'object') return '';
      var name = h.name || h.h || h.hotel || '';
      return name + (name && h.n ? ' ' + h.n + ' kecha' : '');
    }).filter(Boolean).join(' · ');
  }

  /** Services table data from what the collector stored (Mandarin sends hotels, airline, routes, trains). Safo sends none -> null. */
  var MEALS = { BB: 1, HB: 2, FB: 3, AI: 3 };
  function cityName(c) {
    c = String(c || '');
    if (/makk?a/i.test(c)) return 'Makka';
    if (/m[ae]din/i.test(c)) return 'Madina';
    if (/jidd?a|jedd?a/i.test(c)) return 'Jidda';
    return '';
  }
  function liveSvc(pk) {
    var s = pk.services || {};
    var hot = [];
    (Array.isArray(pk.hotels) ? pk.hotels : []).forEach(function (h) {
      if (!h || typeof h !== 'object') return;
      var city = cityName(h.city), name = h.name || h.h || h.hotel;
      if (city && name) hot.push([city, String(name), parseInt(h.nights || h.n, 10) || 0, MEALS[String(h.meal_type || '').toUpperCase()] || 0]);
    });
    var routes = [s.route_go, s.route_ret].filter(Boolean).join(' / ');
    var air = [s.airline, routes].filter(Boolean).join(' · ');
    var trains = Array.isArray(s.trains) ? s.trains.length : 0;
    if (!hot.length && !air && !trains) return null;
    return { air: air, hot: hot, train: trains ? 1 : 0, xfer: '' };
  }

  /** Consecutive weekly departures with the same four prices become one band [from,to,p4,p3,p2,p1]. */
  function buildBands(rows) {
    var sorted = rows.slice().sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
    var bands = [];
    sorted.forEach(function (r) {
      var last = bands[bands.length - 1];
      var same = last && last[2] === r.p[0] && last[3] === r.p[1] && last[4] === r.p[2] && last[5] === r.p[3];
      if (last && same && dayNo(r.date) - dayNo(last[1]) === 7) last[1] = r.date;
      else bands.push([r.date, r.date, r.p[0], r.p[1], r.p[2], r.p[3]]);
    });
    return bands;
  }

  /** Pure function: rows from the database -> the structures the page draws. */
  function buildModel(raw) {
    var today = raw.today;
    var meta = raw.meta || {};
    var price = {}, manual = {};
    (raw.prices || []).forEach(function (r) {
      (price[r.departure_id] = price[r.departure_id] || {})[r.pax] = r.price_usd;
      if (r.is_manual && r.price_usd != null) (manual[r.departure_id] = manual[r.departure_id] || {})[r.pax] = true;
    });
    var depsByPkg = {};
    (raw.departures || []).forEach(function (d) {
      if (d.active === false || !d.date || d.date < today) return;
      (depsByPkg[d.package_id] = depsByPkg[d.package_id] || []).push(d);
    });
    var tuple = function (depId) {
      var p = price[depId] || {};
      return [p[4] == null ? null : p[4], p[3] == null ? null : p[3], p[2] == null ? null : p[2], p[1] == null ? null : p[1]];
    };
    var hasPrice = function (t) { return t.some(function (v) { return v != null; }); };
    var addManual = function (man, depId, date) { if (manual[depId]) man[date] = Object.assign({}, manual[depId]); };

    // Safo does not publish the number of nights; try its day label ("9 kecha"), otherwise 0 = unknown (the page then skips the nights check)
    var nightsFrom = function (pk) {
      if (pk.nights) return pk.nights;
      var m = /(\d+)\s*kecha/i.exec(String((pk.services || {}).days_label || ''));
      return m ? parseInt(m[1], 10) : 0;
    };
    var etihad = [], allpk = [], unmapped = 0;
    (raw.packages || []).forEach(function (pk) {
      if (pk.active === false) return;
      var deps = depsByPkg[pk.id] || [];
      if (pk.company_id === 'B') {
        var rows = [], man = {};
        deps.forEach(function (d) { var t = tuple(d.id); if (hasPrice(t)) { rows.push({ date: d.date, p: t }); addManual(man, d.id, d.date); } });
        if (!rows.length) return;
        rows.sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
        var m = meta[pk.external_key] || {};
        var svc = pk.services || {};
        etihad.push({
          id: pk.external_key,
          name: m.name || pk.name || pk.external_key,
          n: pk.nights || m.n || null,
          days: m.days || (pk.nights ? pk.nights + 1 : null),
          tier: TIER_LABEL[pk.tier] || m.tier || 'Comfort',
          dep: normDay(svc.depday) || weekdayOf(rows[0].date),
          range: m.range || '',
          hotel: m.hotel || hotelsText(pk.hotels),
          cover: m.cover || '', hq: m.hq || '',
          bands: buildBands(rows), man: man, svc: liveSvc(pk)
        });
      } else {
        if (!pk.tier) unmapped += 1;
        var d = {}, cman = {};
        deps.forEach(function (x) {
          if (x.sold_out) return;
          var t = tuple(x.id);
          if (hasPrice(t)) { d[x.date] = t.concat([x.seats_left == null ? null : x.seats_left]); addManual(cman, x.id, x.date); }
        });
        if (!Object.keys(d).length) return;
        allpk.push({ co: pk.company_id, pkg: pk.name || pk.external_key, key: pk.external_key,
                     cat: pk.tier || null, n: nightsFrom(pk), hotel: hotelsText(pk.hotels), svc: liveSvc(pk), d: d, man: cman });
      }
    });
    etihad.sort(function (a, b) { return a.id < b.id ? -1 : 1; });

    var enabled = (raw.sources || []).filter(function (s) { return s.enabled !== false; });
    var times = enabled.map(function (s) { return s.last_ok_at; });
    var oldest = times.length && times.every(Boolean) ? times.slice().sort()[0] : null;
    var down = enabled.filter(function (s) { return (s.consecutive_failures || 0) >= DOWN_AFTER; })
                      .map(function (s) { return NAMES[s.company_id] || s.company_id; });
    var text = oldest ? tashkentText(oldest) : "ma'lumot yo'q";
    if (down.length) text += ' · ' + down.join(', ') + ' ishlamayapti';
    return { etihad: etihad, allpk: allpk, today: today, asOf: oldest, asOfText: text, svc: normSvc(raw.services),
             stats: { unmappedPackages: unmapped, down: down, etihad: etihad.length, competitors: allpk.length } };
  }

  /** Read every row of a table/view, page by page. */
  function fetchAll(client, table, select, order, modify) {
    var out = [];
    function next(from) {
      var q = client.from(table).select(select);
      if (modify) q = modify(q);
      order.forEach(function (c) { q = q.order(c); });
      return q.range(from, from + PAGE - 1).then(function (res) {
        if (res.error) throw new Error(table + ': ' + (res.error.message || 'error'));
        var rows = res.data || [];
        out = out.concat(rows);
        return rows.length < PAGE ? out : next(from + PAGE);
      });
    }
    return next(0);
  }

  /** Services table data. Optional: a missing row, a missing read permission or an error gives empty data. */
  function loadServices(client) {
    return Promise.resolve().then(function () {
      return fetchAll(client, 'app_settings', 'key,value', ['key'], function (q) { return q.eq('key', SERVICES_KEY); });
    }).then(function (rows) { return rows && rows[0] ? rows[0].value : null; }, function () { return null; });
  }

  function load(client, meta, nowMs) {
    var today = tashkentToday(nowMs);
    return Promise.all([
      fetchAll(client, 'packages', 'id,company_id,external_key,name,tier,nights,hotels,services,active', ['id'],
        function (q) { return q.eq('active', true); }),
      fetchAll(client, 'departures', 'id,package_id,date,sold_out,seats_left,active', ['id'],
        function (q) { return q.eq('active', true).gte('date', today); }),
      fetchAll(client, 'price_current', 'departure_id,pax,price_usd,is_manual', ['departure_id', 'pax']),
      fetchAll(client, 'sources', 'id,company_id,enabled,last_ok_at,consecutive_failures', ['id']),
      loadServices(client)
    ]).then(function (r) {
      return buildModel({ packages: r[0], departures: r[1], prices: r[2], sources: r[3], services: r[4], meta: meta, today: today });
    });
  }

  var api = { buildModel: buildModel, buildBands: buildBands, tashkentToday: tashkentToday,
              tashkentText: tashkentText, hotelsText: hotelsText, liveSvc: liveSvc, load: load, fetchAll: fetchAll,
              normDay: normDay, SERVICES_KEY: SERVICES_KEY };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  if (typeof window !== 'undefined' && !window.__UMRA_LIVE_NO_AUTOSTART) {
    window.UmraLive = api;
    var started = false;
    var refresh = function () {
      document.documentElement.classList.add('umra-loaded');      // reveals the page (index.html hides it until then)
      if (typeof window.__umraRefresh === 'function') window.__umraRefresh();
    };
    var boot = function (info) {
      if (started || !info || !info.client) return;
      started = true;
      var run = function () {
        load(info.client, (window.UMRA || {}).etihadMeta).then(function (model) {
          window.UMRA = Object.assign(window.UMRA || {}, model, { loaded: true, loadError: false });
          refresh();
        }).catch(function (e) {
          var msg = String((e && e.message) || e).slice(0, 200);   // table name + database message, no secrets
          if (window.console) window.console.warn('[live] could not load data:', msg);
          window.UMRA = Object.assign(window.UMRA || {}, { loaded: true, loadError: true, loadErrorText: msg });
          refresh();
        });
      };
      run();
      window.setInterval(run, REFRESH_MS);
      document.addEventListener('visibilitychange', function () { if (!document.hidden) run(); });
    };
    window.addEventListener('umra:ready', function (ev) { boot(ev.detail); });
    var cur = window.UmraAuth && window.UmraAuth.instance && window.UmraAuth.instance.ready();
    if (cur) boot(cur);
  }
})();
