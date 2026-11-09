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

    // ---- tab 1: price table ----------------------------------------------------------------
    // Pick a company, pick a package, then every upcoming date is a row with the CURRENT prices in editable cells.
    // Change what you need, press "Saqlash". Changed cells turn gold, so it is always visible what will be saved.
    var LAST_KEY = 'umra.kiritish.last';
    var COLS = [4, 3, 2, 1];
    function priceTab() {
      var companyBox = h('div', { 'class': 'chips', id: 'p-companies' });
      var packageBox = h('div', { 'class': 'chips', id: 'p-packages' });
      var tableBox = h('div', { id: 'p-tablebox', style: 'display:none' });
      var count = h('span', { id: 'p-count', 'class': 'count', text: "O'zgarish yo'q" });
      var days = h('select', { id: 'p-days' }, [h('option', { value: '7', text: '7 kun' }), h('option', { value: '14', text: '14 kun' }),
        h('option', { value: '30', text: '30 kun' })]);
      var btn = h('button', { type: 'submit', 'class': 'main', id: 'p-save', text: 'Saqlash', disabled: 'disabled' });
      var st = status();
      var sel = { co: '', pkg: '' };
      var rows = [];          // {dep, orig: {pax: price}, cells: {pax: input}, sold: checkbox}
      var bulk = {};

      function remember() {
        try { if (deps.storage) deps.storage.setItem(LAST_KEY, JSON.stringify({ co: sel.co, pkg: sel.pkg })); } catch (e) { /* optional */ }
      }
      function chips(box, items, active, onPick) {
        while (box.firstChild) box.removeChild(box.firstChild);
        items.forEach(function (it) {
          var on = String(it[0]) === String(active);
          box.appendChild(h('button', { type: 'button', 'class': 'chip' + (on ? ' on' : ''), 'data-id': String(it[0]), text: it[1],
            on: { click: function () { onPick(it[0]); } } }));
        });
      }

      // ---- what changed? (read the cells) ----
      function collect() {
        var out = { rows: [], n: 0, bad: false, far: 0 };
        var d = parseInt(days.value, 10);
        rows.forEach(function (r) {
          var prices = {}, changed = {};
          COLS.forEach(function (pax) {
            var raw = r.cells[pax].value;
            var v = parsePrice(raw);
            r.cells[pax].className = '';
            if (r.manual[pax]) r.cells[pax].className = 'manual';
            if (String(raw).trim() === '' || v === null) return;            // empty = unchanged
            if (v !== v) { out.bad = true; r.cells[pax].className = 'bad'; return; }
            if (v !== r.orig[pax]) { prices[pax] = v; changed[pax] = true; r.cells[pax].className = 'changed'; }
          });
          var soldOut = r.sold.checked !== !!r.dep.sold_out ? r.sold.checked : null;
          r.tr.className = (Object.keys(changed).length || soldOut !== null) ? 'edited' : '';
          var made = buildEntryRows(r.dep.id, prices, soldOut, null, d, now());
          out.rows = out.rows.concat(made);
          out.n += made.length;
          out.far += bigDeviations(prices, r.orig).length;
        });
        return out;
      }
      function refresh() {
        var c = collect();
        count.textContent = c.bad ? MSG.badPrice : c.n ? c.n + " ta o'zgarish saqlanadi" : "O'zgarish yo'q";
        count.className = 'count' + (c.bad ? ' bad' : c.n ? ' has' : '');
        btn.disabled = !c.n && !c.bad;
        return c;
      }

      // ---- the table ----
      function renderTable(departures, prices) {
        var today = new Date(now()).toISOString().slice(0, 10);
        var upcoming = departures.filter(function (x) { return x.date >= today; });
        var byDep = {};
        prices.forEach(function (r) { (byDep[r.departure_id] = byDep[r.departure_id] || []).push(r); });
        rows = [];
        while (tableBox.firstChild) tableBox.removeChild(tableBox.firstChild);
        if (!upcoming.length) {
          tableBox.appendChild(h('p', { 'class': 'hint', text: "Bu paketda kelgusi sanalar yo'q. «Yangi paket / sana» bo'limida sana qo'shing." }));
          tableBox.style.display = '';
          refresh();
          return;
        }
        // one price for every date: fills the cells, nothing is saved until "Saqlash"
        bulk = {};
        var bulkRow = h('div', { 'class': 'bulk' }, [h('div', { 'class': 'bulk-title', text: "Hamma sanalarga bir xil narx qo'yish (keyin «Saqlash» bosing)" })]);
        var bulkIn = h('div', { 'class': 'bulk-in' });
        COLS.forEach(function (pax) {
          bulk[pax] = h('input', { type: 'text', inputmode: 'numeric', autocomplete: 'off', placeholder: pax + ' kishi', id: 'b-pax' + pax });
          bulkIn.appendChild(bulk[pax]);
        });
        bulkIn.appendChild(h('button', { type: 'button', 'class': 'small', id: 'b-apply', text: 'Hammasiga qo\'yish', on: { click: function () {
          rows.forEach(function (r) {
            if (r.sold.checked) return;
            COLS.forEach(function (pax) { var v = parsePrice(bulk[pax].value); if (v && v === v) r.cells[pax].value = String(v); });
          });
          refresh();
        } } }));
        bulkRow.appendChild(bulkIn);
        tableBox.appendChild(bulkRow);

        var head = h('tr', {}, [h('th', { text: 'Sana' })].concat(COLS.map(function (pax) { return h('th', { text: pax + ' kishi' }); }),
          [h('th', { text: 'Tugagan' })]));
        var body = h('tbody', {});
        upcoming.forEach(function (dep) {
          var cur = byDep[dep.id] || [], orig = {}, manual = {}, conflict = false, cells = {};
          cur.forEach(function (r) { orig[r.pax] = r.price_usd; manual[r.pax] = !!r.is_manual; if (r.conflict) conflict = true; });
          var tds = [h('td', { 'class': 'date', text: dep.date + (conflict ? ' ⚠' : '') })];
          COLS.forEach(function (pax) {
            var inp = h('input', { type: 'text', inputmode: 'numeric', autocomplete: 'off', placeholder: '—', id: 'c-' + dep.id + '-' + pax,
              'aria-label': dep.date + ', ' + pax + ' kishi, USD' });
            inp.value = orig[pax] ? String(orig[pax]) : '';
            inp.addEventListener('input', refresh);
            cells[pax] = inp;
            tds.push(h('td', {}, [inp]));
          });
          var sold = h('input', { type: 'checkbox', id: 's-' + dep.id, 'aria-label': dep.date + ' tugagan' });
          sold.checked = !!dep.sold_out;
          sold.addEventListener('change', refresh);
          tds.push(h('td', { 'class': 'soldcell' }, [sold]));
          var tr = h('tr', {}, tds);
          body.appendChild(tr);
          rows.push({ dep: dep, orig: orig, manual: manual, cells: cells, sold: sold, tr: tr });
        });
        tableBox.appendChild(h('div', { 'class': 'tablewrap' }, [h('table', { id: 'p-table' }, [h('thead', {}, [head]), body])]));
        tableBox.style.display = '';
        refresh();
      }

      // ---- picking ----
      function showCompanies() { chips(companyBox, companyItems(), sel.co, selectCompany); }
      function selectPackage(id) {
        sel.pkg = String(id); remember();
        var list = state.packages[sel.co] || [];
        chips(packageBox, list.map(function (p) { return [p.id, pkgLabel(p)]; }), sel.pkg, selectPackage);
        say(st, '', false);
        return loadDepartures(sel.pkg).then(function (ds) {
          var ids = ds.map(function (x) { return x.id; });
          return (ids.length ? q('price_current').select('departure_id,pax,price_usd,is_manual,conflict').in('departure_id', ids) : Promise.resolve({ data: [] }))
            .then(function (r) { if (r.error) throw r.error; renderTable(ds, r.data || []); });
        }).catch(function () { say(st, MSG.loadFailed, true); });
      }
      function selectCompany(id) {
        sel.co = String(id); sel.pkg = ''; rows = [];
        tableBox.style.display = 'none'; refresh();
        showCompanies();
        while (packageBox.firstChild) packageBox.removeChild(packageBox.firstChild);
        return loadPackages(sel.co).then(function (ps) {
          chips(packageBox, ps.map(function (p) { return [p.id, pkgLabel(p)]; }), '', selectPackage);
          remember();
        }).catch(function () { say(st, MSG.loadFailed, true); });
      }
      function restore() {
        var saved = null;
        try { saved = deps.storage && JSON.parse(deps.storage.getItem(LAST_KEY) || 'null'); } catch (e) { saved = null; }
        if (!saved || !saved.co || !state.companies.some(function (c) { return String(c.id) === String(saved.co); })) return;
        selectCompany(saved.co).then(function () {
          var ok = saved.pkg && (state.packages[sel.co] || []).some(function (p) { return String(p.id) === String(saved.pkg); });
          if (ok) return selectPackage(saved.pkg);
        });
      }

      var form = h('form', { id: 'form-price', novalidate: 'novalidate' }, [
        h('p', { 'class': 'hint', text: "Kompaniyani, so'ng paketni tanlang. Jadvalda kerakli narxni to'g'ridan-to'g'ri o'zgartiring (o'zgargan katak oltin rangda bo'ladi) va «Saqlash» ni bosing." }),
        h('div', { 'class': 'step', text: '1. Kompaniya' }), companyBox,
        h('div', { 'class': 'step', text: '2. Paket' }), packageBox,
        h('div', { 'class': 'step', text: '3. Narxlar (USD, 1 kishi uchun)' }), tableBox,
        h('div', { 'class': 'savebar' }, [count, h('label', { 'for': 'p-days', 'class': 'dayslabel', text: 'Narx amal qiladi:' }), days, btn]), st
      ]);
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var c = refresh();
        if (c.bad) { say(st, MSG.badPrice, true); return; }
        if (!c.n) { say(st, MSG.needPrice, true); return; }
        if (c.far && !confirmFn(c.far + " ta narx hozirgisidan 30% dan ko'proq farq qiladi. Davom etamizmi?")) return;
        btn.disabled = true;
        q('manual_entries').insert(c.rows).then(function (r) {
          if (r.error) { btn.disabled = false; say(st, errorText(r.error), true); return; }
          say(st, MSG.saved + ' ' + c.n + " ta o'zgarish. Saytda bir necha daqiqada ko'rinadi.", false);
          selectPackage(sel.pkg).then(function () { say(st, MSG.saved + ' ' + c.n + " ta o'zgarish. Saytda bir necha daqiqada ko'rinadi.", false); });
          refreshList();
        }).catch(function () { btn.disabled = false; say(st, MSG.failed, true); });
      });
      showCompanies();
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
        h('p', { 'class': 'hint', text: "Yangi sana: kompaniyani va paketni tanlang, sanani yozing. Paket ro'yxatda yo'q bo'lsa — paketni tanlamang, pastda yangi paket ma'lumotlarini kiriting." }),
        field('Kompaniya', co, 'a-company'), field("Qaysi paketga sana qo'shiladi? (yangi paket bo'lsa — bo'sh qoldiring)", existing, 'a-package'), newFields,
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
      var tabs = [['Narxlar', priceTab()], ['Yangi paket / sana', addTab()], ["Bog'lash", matchTab()], ['Yozuvlar', listTab()]];
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
