/* Login gate for the Umra dashboard (Supabase Auth: email + password, TOTP 2FA for admins).
 *
 * - Without a valid session nothing but the login screen is shown.
 * - A deactivated account (profiles.active = false) is signed out. The real lock is Row Level Security in the
 *   database (every policy checks "active"); this only gives a clear message in the UI.
 * - An admin must have a TOTP factor. The database also ignores admin rights in a session without 2FA (migration 5).
 * - "Chiqish" (log out) clears the session AND the service-worker caches.
 */
(function () {
  'use strict';

  var MSG = {
    notConfigured: "Sozlanmagan: config.js faylida Supabase manzili va kaliti yo'q.",
    badLogin: "Email yoki parol noto'g'ri.",
    inactive: "Hisobingiz faol emas. Administrator bilan bog'laning.",
    network: "Ulanishda xatolik. Internetni tekshirib, qayta urinib ko'ring.",
    badKey: "Sozlama xatosi: config.js dagi kalit noto'g'ri (publishable/anon kalit kerak).",
    badCode: "Kod noto'g'ri. Qayta urinib ko'ring.",
    loading: 'Yuklanmoqda…'
  };
  var RECHECK_MS = 5 * 60 * 1000;

  function create(deps) {
    var doc = deps.document, win = deps.window, sb = deps.supabase, cfg = deps.config || {};
    var client = null, timer = null, unlocked = false, info = null;

    // ---- tiny DOM helper (always textContent, never innerHTML with data) -------------
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

    function lock() {
      if (!doc.getElementById('umra-auth-lock')) {
        var s = doc.createElement('style');
        s.id = 'umra-auth-lock';
        s.textContent = 'html{background:#050D0A}body>*:not(#umra-auth):not(#umra-auth-bar){display:none!important}';
        (doc.head || doc.documentElement).appendChild(s);
      }
    }
    function unlock() {
      var s = doc.getElementById('umra-auth-lock');
      if (s) s.remove();
      var root = doc.getElementById('umra-auth');
      if (root) root.remove();
      unlocked = true;
    }
    function injectStyle() {
      if (doc.getElementById('umra-auth-style')) return;
      var s = doc.createElement('style');
      s.id = 'umra-auth-style';
      s.textContent =
        '#umra-auth{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:20px;' +
        'background:radial-gradient(900px 460px at 0% -100px,rgba(26,120,92,.38),transparent 70%),radial-gradient(760px 420px at 100% -60px,rgba(216,177,94,.14),transparent 70%),#050D0A;' +
        'font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:#EAF3EF}' +
        '#umra-auth .card{width:100%;max-width:380px;background:#0A1A15;border:1px solid rgba(216,177,94,.25);border-radius:16px;padding:26px 22px;box-shadow:0 20px 60px rgba(0,0,0,.5)}' +
        '#umra-auth h1{font-family:Fraunces,Georgia,serif;font-size:24px;margin:0 0 4px;color:#D8B15E;font-weight:600}' +
        '#umra-auth p{margin:0 0 16px;font-size:14px;color:#9DB8AE;line-height:1.45}' +
        '#umra-auth label{display:block;font-size:12px;color:#9DB8AE;margin:12px 0 6px}' +
        '#umra-auth input{width:100%;padding:12px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:#06120E;color:#EAF3EF;font-size:16px}' +
        '#umra-auth input:focus{outline:2px solid #D8B15E;outline-offset:1px}' +
        '#umra-auth button.main{width:100%;margin-top:18px;padding:13px;border:0;border-radius:10px;background:#D8B15E;color:#0A1A15;font-weight:700;font-size:15px;cursor:pointer}' +
        '#umra-auth button.main:disabled{opacity:.6;cursor:default}' +
        '#umra-auth button.link{margin-top:12px;background:none;border:0;color:#9DB8AE;font-size:13px;text-decoration:underline;cursor:pointer;padding:0}' +
        '#umra-auth .err{margin-top:12px;font-size:13px;color:#FF8A80;min-height:18px}' +
        '#umra-auth .qr{display:block;margin:10px auto;width:180px;height:180px;background:#fff;border-radius:8px;padding:6px}' +
        '#umra-auth code{display:block;word-break:break-all;font-size:12px;background:#06120E;padding:8px;border-radius:8px;margin-top:8px;color:#D8B15E}' +
        '#umra-auth-bar{position:fixed;top:8px;right:8px;z-index:2147482000;display:flex;gap:8px;align-items:center;font:12px Inter,system-ui,sans-serif;' +
        'background:rgba(5,13,10,.85);border:1px solid rgba(255,255,255,.12);border-radius:999px;padding:5px 6px 5px 12px;color:#9DB8AE;backdrop-filter:blur(6px)}' +
        '#umra-auth-bar button{border:0;border-radius:999px;background:#D8B15E;color:#0A1A15;font-weight:700;padding:5px 12px;cursor:pointer;font-size:12px}';
      (doc.head || doc.documentElement).appendChild(s);
    }

    function screen(title, sub, body) {
      lock(); injectStyle();
      var old = doc.getElementById('umra-auth');
      if (old) old.remove();
      var root = h('div', { id: 'umra-auth' }, [h('div', { 'class': 'card' }, [
        h('h1', { text: title }), sub ? h('p', { text: sub }) : null
      ].concat(body))]);
      (doc.body || doc.documentElement).appendChild(root);
      return root;
    }
    function bar(email, role) {
      var old = doc.getElementById('umra-auth-bar');
      if (old) old.remove();
      var onEntry = /kiritish\.html$/.test((win.location && win.location.pathname) || '');
      var canEdit = role === 'editor' || role === 'admin';
      var nav = (canEdit || onEntry)
        ? h('a', { id: 'umra-nav', href: onEntry ? './index.html' : './kiritish.html', text: onEntry ? '← Narxlar jadvaliga' : "✎ Narxni o'zgartirish",
                   style: 'color:#D8B15E;text-decoration:none;font-weight:700' })
        : null;
      var el = h('div', { id: 'umra-auth-bar' }, [
        nav,
        h('span', { text: email || '' }),
        h('button', { type: 'button', id: 'umra-logout', text: 'Chiqish', on: { click: function () { logout(); } } })
      ]);
      (doc.body || doc.documentElement).appendChild(el);
    }

    // ---- screens ---------------------------------------------------------------------
    function showMessage(text) {
      screen('Umra narxlari', null, [h('p', { text: text, 'class': 'err', id: 'umra-msg' })]);
    }

    function showLogin(note) {
      var email = h('input', { type: 'email', id: 'umra-email', autocomplete: 'username', required: 'required', inputmode: 'email' });
      var pass = h('input', { type: 'password', id: 'umra-pass', autocomplete: 'current-password', required: 'required' });
      var err = h('div', { 'class': 'err', id: 'umra-err', role: 'alert', text: note || '' });
      var btn = h('button', { type: 'submit', 'class': 'main', text: 'Kirish' });
      var form = h('form', { novalidate: 'novalidate' }, [
        h('label', { 'for': 'umra-email', text: 'Email' }), email,
        h('label', { 'for': 'umra-pass', text: 'Parol' }), pass, btn, err
      ]);
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        if (!email.value || !pass.value) { err.textContent = MSG.badLogin; return; }
        btn.disabled = true; err.textContent = '';
        client.auth.signInWithPassword({ email: email.value.trim(), password: pass.value }).then(function (res) {
          if (res.error) {
            // one message for "no such user" and "wrong password": do not reveal which emails exist
            var bad = res.error;
            // an invalid API key is a setup problem (independent of the user), so it is safe to say so
            var keyProblem = bad.status === 401 || /api key|apikey/i.test(bad.message || '');
            err.textContent = keyProblem ? MSG.badKey : (bad.status && bad.status >= 500) ? MSG.network : MSG.badLogin;
            btn.disabled = false; pass.value = '';
            return;
          }
          pass.value = '';
          return proceed();
        }).catch(function () { err.textContent = MSG.network; btn.disabled = false; });
      });
      screen('Umra narxlari', 'Davom etish uchun tizimga kiring.', [form]);
    }

    function codeForm(onSubmit, label) {
      var code = h('input', { type: 'text', id: 'umra-code', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: '6', pattern: '[0-9]*' });
      var err = h('div', { 'class': 'err', id: 'umra-err', role: 'alert' });
      var btn = h('button', { type: 'submit', 'class': 'main', text: 'Tasdiqlash' });
      var form = h('form', { novalidate: 'novalidate' }, [h('label', { 'for': 'umra-code', text: label }), code, btn, err]);
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var v = (code.value || '').replace(/\s/g, '');
        if (!/^\d{6}$/.test(v)) { err.textContent = MSG.badCode; return; }
        btn.disabled = true; err.textContent = '';
        onSubmit(v).then(function (ok) {
          if (!ok) { err.textContent = MSG.badCode; btn.disabled = false; code.value = ''; }
        }).catch(function () { err.textContent = MSG.network; btn.disabled = false; });
      });
      return form;
    }
    function logoutLink() {
      return h('button', { type: 'button', 'class': 'link', id: 'umra-cancel', text: 'Bekor qilish va chiqish', on: { click: function () { logout(); } } });
    }

    function showChallenge(factorId) {
      var form = codeForm(function (code) {
        return client.auth.mfa.challengeAndVerify({ factorId: factorId, code: code }).then(function (r) {
          if (r.error) return false;
          return proceed().then(function () { return true; });
        });
      }, 'Autentifikator ilovasidagi 6 xonali kod');
      screen('Ikki bosqichli tasdiqlash', 'Google Authenticator yoki shunga o\'xshash ilovadagi kodni kiriting.', [form, logoutLink()]);
    }

    function showEnroll() {
      return client.auth.mfa.listFactors().then(function (lf) {
        var stale = ((lf.data && lf.data.all) || []).filter(function (f) { return f.status === 'unverified'; });
        return Promise.all(stale.map(function (f) { return client.auth.mfa.unenroll({ factorId: f.id }); }));
      }).then(function () {
        return client.auth.mfa.enroll({ factorType: 'totp' });
      }).then(function (r) {
        if (r.error || !r.data) { showLogin(MSG.network); return; }
        var d = r.data;
        var raw = (d.totp && d.totp.qr_code) || '';
        // Supabase already returns a ready data: URI; older versions return bare <svg>. Only an <img> ever shows it (no scripts run).
        var src = /^data:image\/svg\+xml/i.test(raw) ? raw : 'data:image/svg+xml;utf8,' + encodeURIComponent(raw);
        var qr = h('img', { 'class': 'qr', alt: 'QR kod', src: src });
        var secret = h('code', { text: (d.totp && d.totp.secret) || '', id: 'umra-secret' });
        var form = codeForm(function (code) {
          return client.auth.mfa.challengeAndVerify({ factorId: d.id, code: code }).then(function (v) {
            if (v.error) return false;
            return proceed().then(function () { return true; });
          });
        }, 'Ilovada paydo bo\'lgan 6 xonali kod');
        screen('Ikki bosqichli himoyani yoqing',
          'Administrator hisobi uchun majburiy. QR-kodni Google Authenticator (yoki shunga o\'xshash ilova)da skanerlang, so\'ng kodni kiriting.',
          [qr, secret, form, logoutLink()]);
      });
    }

    // ---- flow ------------------------------------------------------------------------
    function clearCaches() {
      if (!win.caches || !win.caches.keys) return Promise.resolve();
      return win.caches.keys().then(function (ks) { return Promise.all(ks.map(function (k) { return win.caches.delete(k); })); })
        .catch(function () {});
    }

    function logout() {
      if (timer) { win.clearInterval(timer); timer = null; }
      var done = function () { return clearCaches().then(function () { win.location.reload(); }); };
      return client.auth.signOut().then(done, done);
    }

    function proceed() {
      return client.auth.getSession().then(function (s) {
        var session = s.data && s.data.session;
        if (!session) { showLogin(); return; }
        return client.from('profiles').select('role,active').eq('user_id', session.user.id).maybeSingle().then(function (p) {
          if (p.error && p.error.status && p.error.status >= 500) { showMessage(MSG.network); return; }
          var prof = p.data;
          if (!prof || !prof.active) {
            return client.auth.signOut().then(function () { return clearCaches(); }).then(function () { showLogin(MSG.inactive); });
          }
          return client.auth.mfa.getAuthenticatorAssuranceLevel().then(function (a) {
            var lvl = a.data || {};
            if (lvl.nextLevel === 'aal2' && lvl.currentLevel !== 'aal2') {
              return client.auth.mfa.listFactors().then(function (lf) {
                var f = lf.data && lf.data.totp && lf.data.totp[0];
                if (f) showChallenge(f.id); else showEnroll();
              });
            }
            if (prof.role === 'admin' && lvl.currentLevel !== 'aal2') return showEnroll();
            unlock();
            bar(session.user.email, prof.role);
            info = { client: client, role: prof.role, userId: session.user.id, email: session.user.email };
            if (typeof win.dispatchEvent === 'function' && win.CustomEvent) win.dispatchEvent(new win.CustomEvent('umra:ready', { detail: info }));
            if (!timer) timer = win.setInterval(recheck, RECHECK_MS);
          });
        });
      }).catch(function () { showMessage(MSG.network); });
    }

    function recheck() {
      if (!unlocked) return;
      client.auth.getSession().then(function (s) {
        if (!(s.data && s.data.session)) { win.location.reload(); return; }
        return client.from('profiles').select('active').eq('user_id', s.data.session.user.id).maybeSingle().then(function (p) {
          if (!p.error && (!p.data || !p.data.active)) return logout();
        });
      }).catch(function () {});
    }

    function start() {
      lock();
      if (!sb || !cfg.supabaseUrl || !cfg.anonKey) { showMessage(MSG.notConfigured); return Promise.resolve(); }
      client = sb.createClient(cfg.supabaseUrl, cfg.anonKey, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false }
      });
      client.auth.onAuthStateChange(function (event) {
        if (event === 'SIGNED_OUT' && unlocked) win.location.reload();   // signed out in another tab
      });
      showMessage(MSG.loading);
      return proceed();
    }

    return { start: start, logout: logout, _client: function () { return client; }, ready: function () { return info; } };
  }

  var api = { create: create, MSG: MSG };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.UmraAuth = api;
    if (!window.__UMRA_AUTH_NO_AUTOSTART) {
      // lock immediately (before <body> exists) so the app never flashes, then start when the DOM is ready
      var go = function () {
        window.UmraAuth.instance = create({ document: document, window: window, supabase: window.supabase, config: window.UMRA_CONFIG });
        window.UmraAuth.instance.start();
      };
      var s = document.createElement('style');
      s.id = 'umra-auth-lock';
      s.textContent = 'html{background:#050D0A}body>*:not(#umra-auth):not(#umra-auth-bar){display:none!important}';
      document.head.appendChild(s);
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
    }
  }
})();
