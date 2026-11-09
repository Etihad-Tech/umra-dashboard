const { JSDOM } = require('jsdom');

/** A page with a fake "app" in it, so we can check that it stays hidden while locked. */
function makeDom() {
  const dom = new JSDOM('<!doctype html><html><head></head><body><x-dc id="app">SECRET PRICES</x-dc></body></html>');
  return dom.window.document;
}

/** Fake Supabase client. Everything it does is recorded in `calls`. */
function fakeSupabase(opts = {}) {
  const o = Object.assign({
    session: null,                 // {user:{id,email}} or null
    profile: { role: 'viewer', active: true },
    profileError: null,
    levels: { currentLevel: 'aal1', nextLevel: 'aal1' },
    factors: { totp: [], all: [] },
    signIn: { error: null },
    verifyOk: true,
  }, opts);
  const calls = [];
  let listener = null;
  const client = {
    auth: {
      getSession: async () => ({ data: { session: o.session } }),
      signInWithPassword: async (cred) => {
        calls.push(['signIn', cred.email]);
        if (!o.signIn.error) o.session = { user: { id: 'u1', email: cred.email } };
        return o.signIn;
      },
      signOut: async () => { calls.push(['signOut']); o.session = null; return { error: null }; },
      onAuthStateChange: (cb) => { listener = cb; },
      mfa: {
        getAuthenticatorAssuranceLevel: async () => ({ data: o.levels }),
        listFactors: async () => ({ data: o.factors }),
        unenroll: async ({ factorId }) => { calls.push(['unenroll', factorId]); return {}; },
        enroll: async () => {
          calls.push(['enroll']);
          return { data: { id: 'f-new', totp: { qr_code: '<svg><script>alert(1)</script></svg>', secret: 'ABCDEF234567' } } };
        },
        challengeAndVerify: async ({ factorId, code }) => {
          calls.push(['verify', factorId, code]);
          if (!o.verifyOk) return { error: { message: 'bad' } };
          o.levels = { currentLevel: 'aal2', nextLevel: 'aal2' };
          return { data: {} };
        },
      },
    },
    from: (table) => {
      calls.push(['from', table]);
      const q = { select() { return q; }, eq() { return q; },
        maybeSingle: async () => ({ data: o.profile, error: o.profileError }) };
      return q;
    },
  };
  return {
    sdk: { createClient: () => client },
    calls, opts: o,
    emit: (event) => listener && listener(event),
  };
}

function fakeWindow() {
  const w = {
    reloads: 0, cachesDeleted: [], intervals: [],
    location: { reload() { w.reloads++; } },
    caches: { keys: async () => ['umra-v3', 'other'], delete: async (k) => { w.cachesDeleted.push(k); return true; } },
    setInterval(fn, ms) { w.intervals.push(ms); return 1; },
    clearInterval() {},
  };
  return w;
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 8; i++) await tick(); }

function submit(doc, selector) {
  const form = doc.querySelector(selector);
  form.dispatchEvent(new doc.defaultView.Event('submit', { cancelable: true, bubbles: true }));
}

module.exports = { makeDom, fakeSupabase, fakeWindow, settle, submit };
