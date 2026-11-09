/* Manual entry page (editors and admins): prices, new packages/dates, package matches, active manual entries.
 *
 * Everything the user types goes to Supabase through the logged-in client. The real access control is Row Level
 * Security + column grants in the database (migration 6); the role check here only hides a page the user could
 * not use anyway. All text is written with textContent, never innerHTML.
 */
(function () {
  'use strict';

  var MSG = {
    noAccess: "Bu sahifa faqat muharrir va administrator uchun.",
    saved: 'Saqlandi.',
    failed: "Saqlanmadi. Qayta urinib ko'ring.",
    duplicate: 'Bunday yozuv allaqachon mavjud.',
    denied: "Ruxsat yo'q (yoki sessiya tugagan). Qayta kiring.",
    needPrice: "Kamida bitta narx yoki 'tugagan' belgisini kiriting.",
    badPrice: "Narx butun son bo'lishi kerak (USD, 0 dan katta).",
    pick: 'Avval paket va sanani tanlang.',
    loadFailed: "Ma'lumotni yuklab bo'lmadi."
  };
  var TIERS = [['premium', 'Premium'], ['lux', 'Lux'], ['comfort', 'Komfort'], ['econom', 'Ekonom']];
  var DEVIATION = 0.3;   // ask for confirmation when a price differs from the current one by more than 30%

  // ------------------------------------------------------------------ pure helpers
  /** "1 190", "1190", "$1,190" -> 1190; '' -> null; anything else -> NaN. */
  function parsePrice(s) {
    s = String(s == null ? '' : s).replace(/[\s$,]/g, '');
    if (s === '') return null;
    if (!/^\d+$/.test(s)) return NaN;
    var n = parseInt(s, 10);
    return n > 0 ? n : NaN;
  }
  /** rows for manual_entries. prices = {1:..,2:..,3:..,4:..} (numbers or null); soldOut = true | false | null. */
  function buildEntryRows(departureId, prices, soldOut, note, days, now) {
    var until = new Date((now || Date.now()) + days * 86400000).toISOString();
    var base = { departure_id: departureId, note: note || null, valid_until: until };
    var rows = [];
    [1, 2, 3, 4].forEach(function (pax) {
      if (prices[pax]) rows.push(Object.assign({ pax: pax, price_usd: prices[pax] }, base));
    });
    if (soldOut === true || soldOut === false) rows.push(Object.assign({ sold_out: soldOut }, base));
    return rows;
  }
  /** pax numbers whose new price is far from the current one. */
  function bigDeviations(prices, current) {
    var out = [];
    Object.keys(prices).forEach(function (pax) {
      var n = prices[pax], cur = current[pax];
      if (n && cur && Math.abs(n - cur) / cur > DEVIATION) out.push(Number(pax));
    });
    return out;
  }
  function errorText(err) {
    if (!err) return '';
    if (err.code === '23505') return MSG.duplicate;
    if (err.code === '42501' || err.status === 401 || err.status === 403) return MSG.denied;
    return MSG.failed;
  }

  // -------------------------------------------------------------------------- page
  function create(deps) {
    var doc = deps.document, client = deps.client, root = deps.root;
    var confirmFn = deps.confirm || function () { return true; };
    var now = deps.now || function () { return Date.now(); };
    var state = { companies: [], packages: {} };

    function h(tag, attrs, kids) {
      var el = doc.createElement(tag);
      Object.keys(attrs || {}).forEach(function (k) {
        if (k === 'text') el.textContent = attrs[k];
        else if (k === 'on') Object.keys(attrs.on).forEach(function (ev) { el.addEventListener(ev, attrs.on[ev]); });
        else el.setAttribute(k, attrs[k]);
      });
      (kids || []).forEach(function (c) { if (c) el.appendChild(c); });
      return el;
    }
    function field(label, input, id) {
      input.id = id;
      return h('div', { 'class': 'f' }, [h('label', { 'for': id, text: label }), input]);
    }
    function select(id, items, placeholder) {
      var s = h('select', {});
      s.id = id;
      setOptions(s, items, placeholder);
      return s;
    }
    function setOptions(s, items, placeholder) {
      while (s.firstChild) s.removeChild(s.firstChild);
      s.appendChild(h('option', { value: '', text: placeholder || '—' }));
      items.forEach(function (it) { s.appendChild(h('option', { value: String(it[0]), text: it[1] })); });
    }
    function status() { return h('div', { 'class': 'status', role: 'status' }); }
    function say(el, text, bad) { el.textContent = text; el.className = 'status' + (bad ? ' bad' : ' ok'); }
    function q(table) { return client.from(table); }

    // ---- data ------------------------------------------------------------------------
    function loadCompanies() {
      return q('companies').select('id,name,short').order('id').then(function (r) {
        if (r.error) throw r.error;
        state.companies = r.data || [];
        return state.companies;
      });
    }
    function loadPackages(companyId) {
      return q('packages').select('id,company_id,external_key,name,tier,nights,departure_city,is_manual')
        .eq('company_id', companyId).eq('active', true).order('external_key').then(function (r) {
          if (r.error) throw r.error;
          state.packages[companyId] = r.data || [];
          return state.packages[companyId];
        });
    }
    function loadDepartures(packageId) {
      return q('departures').select('id,date,sold_out,is_manual').eq('package_id', packageId).eq('active', true)
        .order('date').then(function (r) { if (r.error) throw r.error; return r.data || []; });
    }
    function loadCurrent(departureId) {
      return q('price_current').select('pax,price_usd,is_manual,conflict').eq('departure_id', departureId)
        .then(function (r) { if (r.error) throw r.error; return r.data || []; });
    }
    function pkgLabel(p) {
      return p.external_key + (p.name ? ' · ' + p.name : '') + (p.departure_city ? ' · ' + p.departure_city : '') + (p.is_manual ? ' ✎' : '');
    }
    function companyItems() { return state.companies.map(function (c) { return [c.id, c.short || c.name]; }); }

    // ---- tab 1: price entry ------------------------------------------------------------
    // Fewer clicks: the last company/package is remembered, the nearest upcoming date is already chosen,
    // current prices are shown as grey hints (type only what changed) and one price can go to ALL dates.
    var LAST_KEY = 'umra.kiritish.last';
    function priceTab() {
      var co = select('p-company', companyItems(), 'Kompaniya');
      var pkg = select('p-package', [], 'Paket');
      var dep = select('p-departure', [], 'Sana');
      var cur = h('div', { 'class': 'cur', id: 'p-current' });
      var inputs = {};
      var priceFields = [1, 2, 3, 4].map(function (pax) {
        inputs[pax] = h('input', { type: 'text', inputmode: 'numeric', autocomplete: 'off' });
        return field(pax + ' kishi, USD', inputs[pax], 'p-pax' + pax);
      });
      var sold = select('p-sold', [['yes', 'Ha, tugagan'], ['no', "Yo'q, joy bor"]], "Joy holati: o'zgarmasin");
      var note = h('input', { type: 'text', maxlength: '200' });
      var days = h('input', { type: 'number', min: '1', max: '30', value: '7' });
      var allBox = h('input', { type: 'checkbox', id: 'p-all', style: 'width:auto;margin-right:8px' });
      var allLabel = h('label', { 'for': 'p-all', id: 'p-alllabel', style: 'display:inline', text: '' });
      var allRow = h('div', { 'class': 'f', id: 'p-allrow', style: 'display:none' }, [allBox, allLabel]);
      var st = status();
      var current = {}, depList = [];

      function remember() {
        try { if (deps.storage) deps.storage.setItem(LAST_KEY, JSON.stringify({ co: co.value, pkg: pkg.value })); } catch (e) { /* optional */ }
      }
      function hasOption(sel, v) { return Array.prototype.some.call(sel.options, function (o) { return o.value === String(v); }); }
      function onCompany() {
        setOptions(pkg, [], 'Paket'); setOptions(dep, [], 'Sana'); cur.textContent = ''; depList = []; allRow.style.display = 'none';
        if (!co.value) return Promise.resolve();
        return loadPackages(co.value).then(function (ps) { setOptions(pkg, ps.map(function (p) { return [p.id, pkgLabel(p)]; }), 'Paket'); })
          .catch(function () { say(st, MSG.loadFailed, true); });
      }
      function onDeparture() {
        current = {}; cur.textContent = '';
        [1, 2, 3, 4].forEach(function (pax) { inputs[pax].placeholder = ''; });
        if (!dep.value) return Promise.resolve();
        return loadCurrent(dep.value).then(function (rows) {
          rows.forEach(function (r) { current[r.pax] = r.price_usd; inputs[r.pax].placeholder = String(r.price_usd); });
          cur.textContent = rows.length
            ? 'Hozirgi narxlar: ' + rows.map(function (r) {
                return r.pax + ' kishi $' + r.price_usd + (r.is_manual ? ' (qo\'lda)' : '') + (r.conflict ? ' ⚠' : '');
              }).join(', ')
            : "Hozircha narx yo'q.";
        }).catch(function () { say(st, MSG.loadFailed, true); });
      }
      function onPackage() {
        setOptions(dep, [], 'Sana'); cur.textContent = ''; depList = []; allBox.checked = false; allRow.style.display = 'none';
        if (!pkg.value) return Promise.resolve();
        remember();
        return loadDepartures(pkg.value).then(function (ds) {
          depList = ds;
          setOptions(dep, ds.map(function (d) { return [d.id, d.date + (d.sold_out ? ' (tugagan)' : '')]; }), 'Sana');
          if (ds.length > 1) { allLabel.textContent = "Barcha sanalarga qo'llash (" + ds.length + ' ta)'; allRow.style.display = ''; }
          var today = new Date(now()).toISOString().slice(0, 10);
          var upcoming = ds.filter(function (d) { return d.date >= today; });
          var next = upcoming.filter(function (d) { return !d.sold_out; })[0] || upcoming[0];
          if (next) { dep.value = String(next.id); return onDeparture(); }
        }).catch(function () { say(st, MSG.loadFailed, true); });
      }
      function restore() {
        var saved = null;
        try { saved = deps.storage && JSON.parse(deps.storage.getItem(LAST_KEY) || 'null'); } catch (e) { saved = null; }
        if (!saved || !saved.co || !hasOption(co, saved.co)) return;
        co.value = saved.co;
        onCompany().then(function () {
          if (saved.pkg && hasOption(pkg, saved.pkg)) { pkg.value = String(saved.pkg); return onPackage(); }
        });
      }

      co.addEventListener('change', function () { onCompany().then(function () { remember(); }); });
      pkg.addEventListener('change', onPackage);
      dep.addEventListener('change', onDeparture);

      var btn = h('button', { type: 'submit', 'class': 'main', text: 'Saqlash' });
      var form = h('form', { id: 'form-price', novalidate: 'novalidate' },
        [field('Kompaniya', co, 'p-company'), field('Paket', pkg, 'p-package'), field('Sana', dep, 'p-departure'), allRow, cur]
          .concat(priceFields, [field('Joy holati', sold, 'p-sold'), field('Izoh', note, 'p-note'),
            field('Necha kun amal qiladi (1–30)', days, 'p-days'), btn, st]));
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var all = allBox.checked && depList.length > 1;
        if (!all && !dep.value) { say(st, MSG.pick, true); return; }
        var prices = {}, bad = false;
        [1, 2, 3, 4].forEach(function (pax) {
          var v = parsePrice(inputs[pax].value);
          if (v !== v) bad = true;          // NaN
          prices[pax] = v;
        });
        if (bad) { say(st, MSG.badPrice, true); return; }
        var soldOut = sold.value === 'yes' ? true : sold.value === 'no' ? false : null;
        var d = parseInt(days.value, 10);
        if (!(d >= 1 && d <= 30)) d = 7;
        var targets = all ? depList.map(function (x) { return x.id; }) : [Number(dep.value)];
        var rows = [];
        targets.forEach(function (id) { rows = rows.concat(buildEntryRows(id, prices, soldOut, note.value.trim(), d, now())); });
        if (!rows.length) { say(st, MSG.needPrice, true); return; }
        var far = bigDeviations(prices, current);
        if (far.length && !confirmFn("Narx hozirgisidan 30% dan ko'proq farq qiladi (" + far.join(', ') + " kishi). Davom etamizmi?")) return;
        if (all && !confirmFn('Narx ' + targets.length + " ta sanaga qo'llanadi. Davom etamizmi?")) return;
        btn.disabled = true;
        q('manual_entries').insert(rows).then(function (r) {
          btn.disabled = false;
          if (r.error) { say(st, errorText(r.error), true); return; }
          [1, 2, 3, 4].forEach(function (pax) { inputs[pax].value = ''; });
          allBox.checked = false;
          say(st, MSG.saved, false);
          onDeparture();
          refreshList();
        }).catch(function () { btn.disabled = false; say(st, MSG.failed, true); });
      });
      restore();
      return form;
    }

    // ---- tab 2: new package / new date --------------------------------------------------
    function addTab() {
      var co = select('a-company', companyItems(), 'Kompaniya');
      var existing = select('a-package', [], 'Yangi paket yaratish');
      var key = h('input', { type: 'text', maxlength: '60' });
      var name = h('input', { type: 'text', maxlength: '120' });
      var tier = select('a-tier', TIERS, 'Kategoriya');
      var nights = h('input', { type: 'number', min: '1', max: '60' });
      var city = h('input', { type: 'text', maxlength: '60' });
      var date = h('input', { type: 'date' });
      var ret = h('input', { type: 'date' });
      var st = status();
      var btn = h('button', { type: 'submit', 'class': 'main', text: "Qo'shish" });
      var newFields = h('div', { id: 'a-newpkg' }, [field('Paket kodi', key, 'a-key'), field('Nomi', name, 'a-name'), field('Kategoriya', tier, 'a-tier'),
        field('Kechalar soni', nights, 'a-nights'), field("Chiqish shahri", city, 'a-city')]);

      co.addEventListener('change', function () {
        setOptions(existing, [], 'Yangi paket yaratish');
        if (!co.value) return;
        loadPackages(co.value).then(function (ps) { setOptions(existing, ps.map(function (p) { return [p.id, pkgLabel(p)]; }), 'Yangi paket yaratish'); })
          .catch(function () { say(st, MSG.loadFailed, true); });
      });
      existing.addEventListener('change', function () { newFields.style.display = existing.value ? 'none' : ''; });

      var form = h('form', { id: 'form-add', novalidate: 'novalidate' }, [
        field('Kompaniya', co, 'a-company'), field("Mavjud paketga sana qo'shish", existing, 'a-package'), newFields,
        field('Chiqish sanasi', date, 'a-date'), field('Qaytish sanasi (ixtiyoriy)', ret, 'a-return'), btn, st]);
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        if (!co.value || !date.value) { say(st, "Kompaniya va sanani kiriting.", true); return; }
        if (ret.value && ret.value < date.value) { say(st, "Qaytish sanasi chiqishdan oldin bo'lmasligi kerak.", true); return; }
        var makeDeparture = function (packageId) {
          return q('departures').insert({ package_id: packageId, date: date.value, return_date: ret.value || null, is_manual: true });
        };
        btn.disabled = true;
        var work;
        if (existing.value) {
          work = makeDeparture(Number(existing.value));
        } else {
          if (!key.value.trim()) { btn.disabled = false; say(st, 'Paket kodini kiriting.', true); return; }
          var n = nights.value ? parseInt(nights.value, 10) : null;
          work = q('packages').insert({
            company_id: co.value, external_key: key.value.trim(), name: name.value.trim() || null, tier: tier.value || null,
            nights: n > 0 ? n : null, departure_city: city.value.trim() || null, is_manual: true
          }).select('id').single().then(function (r) {
            if (r.error) return r;
            return makeDeparture(r.data.id);
          });
        }
        work.then(function (r) {
          btn.disabled = false;
          if (r.error) { say(st, errorText(r.error), true); return; }
          say(st, MSG.saved, false);
          delete state.packages[co.value];
          co.dispatchEvent(new doc.defaultView.Event('change'));
        }).catch(function () { btn.disabled = false; say(st, MSG.failed, true); });
      });
      return form;
    }

    // ---- tab 3: package matches ---------------------------------------------------------
    function matchTab() {
      var ours = select('m-our', [], 'Bizning paket (ETIHAD)');
      var theirCo = select('m-company', companyItems().filter(function (c) { return c[0] !== 'B'; }), 'Raqobatchi');
      var theirs = select('m-their', [], 'Raqobatchi paketi');
      var note = h('input', { type: 'text', maxlength: '200' });
      var st = status();
      var list = h('ul', { id: 'm-list', 'class': 'list' });
      var btn = h('button', { type: 'submit', 'class': 'main', text: 'Bog\'lash' });

      loadPackages('B').then(function (ps) { setOptions(ours, ps.map(function (p) { return [p.id, pkgLabel(p)]; }), 'Bizning paket (ETIHAD)'); refresh(); })
        .catch(function () { say(st, MSG.loadFailed, true); });
      theirCo.addEventListener('change', function () {
        setOptions(theirs, [], 'Raqobatchi paketi');
        if (!theirCo.value) return;
        loadPackages(theirCo.value).then(function (ps) { setOptions(theirs, ps.map(function (p) { return [p.id, pkgLabel(p)]; }), 'Raqobatchi paketi'); refresh(); })
          .catch(function () { say(st, MSG.loadFailed, true); });
      });
      function nameOf(id) {
        var all = [].concat.apply([], Object.keys(state.packages).map(function (k) { return state.packages[k]; }));
        var p = all.filter(function (x) { return x.id === id; })[0];
        return p ? p.company_id + ' · ' + pkgLabel(p) : '#' + id;
      }
      function refresh() {
        return q('package_matches').select('id,our_package_id,their_package_id,note').eq('active', true).order('id').then(function (r) {
          if (r.error) { say(st, MSG.loadFailed, true); return; }
          while (list.firstChild) list.removeChild(list.firstChild);
          (r.data || []).forEach(function (m) {
            list.appendChild(h('li', {}, [
              h('span', { text: nameOf(m.our_package_id) + '  ⇄  ' + nameOf(m.their_package_id) + (m.note ? ' — ' + m.note : '') }),
              h('button', { type: 'button', 'class': 'small', text: 'Uzish', on: { click: function () {
                q('package_matches').update({ active: false }).eq('id', m.id).then(function (u) {
                  if (u.error) say(st, errorText(u.error), true); else refresh();
                });
              } } })
            ]));
          });
        });
      }
      var form = h('form', { id: 'form-match', novalidate: 'novalidate' }, [
        field('Bizning paket', ours, 'm-our'), field('Raqobatchi', theirCo, 'm-company'), field('Raqobatchi paketi', theirs, 'm-their'),
        field('Izoh', note, 'm-note'), btn, st, list]);
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        if (!ours.value || !theirs.value) { say(st, 'Ikkala paketni tanlang.', true); return; }
        btn.disabled = true;
        q('package_matches').insert({ our_package_id: Number(ours.value), their_package_id: Number(theirs.value), note: note.value.trim() || null })
          .then(function (r) {
            btn.disabled = false;
            if (r.error) { say(st, errorText(r.error), true); return; }
            say(st, MSG.saved, false); note.value = ''; refresh();
          }).catch(function () { btn.disabled = false; say(st, MSG.failed, true); });
      });
      return form;
    }

    // ---- tab 4: active manual entries -----------------------------------------------------
    var listBox = null, listStatus = null;
    function refreshList() {
      if (!listBox) return Promise.resolve();
      var nowIso = new Date(now()).toISOString();
      return q('manual_entries')
        .select('id,pax,price_usd,sold_out,note,created_at,valid_until,created_by,departure_id,departures(date,packages(external_key,name,company_id))')
        .is('cleared_at', null).gt('valid_until', nowIso).order('created_at', { ascending: false }).then(function (r) {
          if (r.error) { say(listStatus, MSG.loadFailed, true); return; }
          var rows = r.data || [];
          var ids = rows.map(function (x) { return x.departure_id; });
          return Promise.all([
            q('profile_names').select('user_id,display_name'),
            ids.length ? q('price_current').select('departure_id,pax,conflict').in('departure_id', ids) : Promise.resolve({ data: [] })
          ]).then(function (res) {
            var names = {}, conflicts = {};
            ((res[0] && res[0].data) || []).forEach(function (n) { names[n.user_id] = n.display_name; });
            ((res[1] && res[1].data) || []).forEach(function (c) { if (c.conflict) conflicts[c.departure_id + '|' + c.pax] = true; });
            while (listBox.firstChild) listBox.removeChild(listBox.firstChild);
            if (!rows.length) listBox.appendChild(h('li', { text: "Faol qo'lda yozuvlar yo'q." }));
            rows.forEach(function (e) {
              var dep = e.departures || {}, pk = dep.packages || {};
              var what = e.price_usd ? e.pax + ' kishi: $' + e.price_usd : (e.sold_out ? 'Tugagan' : "Joy bor");
              var line = (pk.company_id || '') + ' · ' + (pk.external_key || '?') + ' · ' + (dep.date || '?') + ' — ' + what +
                '  (' + (names[e.created_by] || '?') + ', ' + String(e.created_at).slice(0, 16).replace('T', ' ') +
                ', ' + String(e.valid_until).slice(0, 10) + " gacha)" + (e.note ? ' — ' + e.note : '');
              var warn = e.pax && conflicts[e.departure_id + '|' + e.pax];
              listBox.appendChild(h('li', warn ? { 'class': 'conflict' } : {}, [
                h('span', { text: (warn ? '⚠ Saytda boshqa narx: ' : '') + line }),
                h('button', { type: 'button', 'class': 'small', text: "O'chirish", on: { click: function () {
                  q('manual_entries').update({ cleared_at: new Date(now()).toISOString() }).eq('id', e.id).then(function (u) {
                    if (u.error) say(listStatus, errorText(u.error), true); else refreshList();
                  });
                } } })
              ]));
            });
          });
        }).catch(function () { say(listStatus, MSG.loadFailed, true); });
    }
    function listTab() {
      listStatus = status();
      listBox = h('ul', { id: 'e-list', 'class': 'list' });
      return h('div', { id: 'tab-list' }, [listStatus, listBox]);
    }

    // ---- shell ---------------------------------------------------------------------------
    function render() {
      var tabs = [['Narx', priceTab()], ['Paket / sana', addTab()], ['Bog\'lash', matchTab()], ['Yozuvlar', listTab()]];
      var nav = h('div', { 'class': 'tabs', role: 'tablist' });
      var panels = tabs.map(function (t, i) {
        var panel = h('section', { 'class': 'panel', id: 'panel-' + i }, [t[1]]);
        panel.style.display = i === 0 ? '' : 'none';
        nav.appendChild(h('button', { type: 'button', role: 'tab', id: 'tab-' + i, text: t[0], on: { click: function () {
          panels.forEach(function (p, j) { p.style.display = j === i ? '' : 'none'; });
          if (i === 3) refreshList();
        } } }));
        return panel;
      });
      while (root.firstChild) root.removeChild(root.firstChild);
      root.appendChild(h('h1', { text: "Qo'lda kiritish" }));
      root.appendChild(nav);
      panels.forEach(function (p) { root.appendChild(p); });
    }

    function start() {
      return loadCompanies().then(render).catch(function () {
        while (root.firstChild) root.removeChild(root.firstChild);
        root.appendChild(h('p', { 'class': 'status bad', text: MSG.loadFailed }));
      });
    }
    return { start: start, refreshList: refreshList };
  }

  var api = { create: create, MSG: MSG, parsePrice: parsePrice, buildEntryRows: buildEntryRows, bigDeviations: bigDeviations, errorText: errorText };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  if (typeof window !== 'undefined' && !window.__UMRA_KIRITISH_NO_AUTOSTART) {
    var boot = function (info) {
      var root = document.getElementById('kiritish');
      if (!root) return;
      if (info.role !== 'editor' && info.role !== 'admin') { root.textContent = MSG.noAccess; return; }
      var storage = null;
      try { storage = window.localStorage; } catch (e) { storage = null; }
      create({ document: document, client: info.client, root: root, storage: storage, confirm: function (m) { return window.confirm(m); } }).start();
    };
    window.addEventListener('umra:ready', function (ev) { boot(ev.detail); });
    var a = window.UmraAuth && window.UmraAuth.instance && window.UmraAuth.instance.ready();
    if (a) boot(a);
  }
})();
