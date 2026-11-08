const test = require('node:test');
const assert = require('node:assert');
const { create, MSG } = require('../auth.js');
const { makeDom, fakeSupabase, fakeWindow, settle, submit } = require('./helpers');

const CONFIG = { supabaseUrl: 'https://x.supabase.co', anonKey: 'anon' };
const SESSION = { user: { id: 'u1', email: 'a@b.uz' } };

async function boot(opts, config = CONFIG) {
  const doc = makeDom(), win = fakeWindow(), fake = fakeSupabase(opts);
  const auth = create({ document: doc, window: win, supabase: fake.sdk, config });
  await auth.start();
  await settle();
  return { doc, win, fake, auth };
}
const locked = (doc) => !!doc.getElementById('umra-auth-lock');
const text = (doc, id) => (doc.getElementById(id) || {}).textContent;
const fill = (doc, id, v) => { doc.getElementById(id).value = v; };

test('not configured: only a message, app stays hidden', async () => {
  const { doc } = await boot({}, {});
  assert.ok(locked(doc));
  assert.match(text(doc, 'umra-msg'), /Sozlanmagan/);
});

test('no session: login form, app hidden', async () => {
  const { doc } = await boot({ session: null });
  assert.ok(locked(doc));
  assert.ok(doc.getElementById('umra-email') && doc.getElementById('umra-pass'));
});

test('wrong password: one generic message, still locked, password cleared', async () => {
  const { doc, fake } = await boot({ session: null, signIn: { error: { status: 400, message: 'Invalid login credentials' } } });
  fill(doc, 'umra-email', 'x@y.uz'); fill(doc, 'umra-pass', 'secret');
  submit(doc, 'form'); await settle();
  assert.equal(text(doc, 'umra-err'), MSG.badLogin);
  assert.equal(doc.getElementById('umra-pass').value, '');
  assert.ok(locked(doc));
  assert.ok(!fake.calls.some((c) => c[0] === 'from'), 'must not load data after a failed login');
});

test('unknown email and wrong password give the same message', async () => {
  const a = await boot({ session: null, signIn: { error: { status: 400, message: 'User not found' } } });
  fill(a.doc, 'umra-email', 'no@one.uz'); fill(a.doc, 'umra-pass', 'x'); submit(a.doc, 'form'); await settle();
  const b = await boot({ session: null, signIn: { error: { status: 400, message: 'Invalid login credentials' } } });
  fill(b.doc, 'umra-email', 'me@one.uz'); fill(b.doc, 'umra-pass', 'x'); submit(b.doc, 'form'); await settle();
  assert.equal(text(a.doc, 'umra-err'), text(b.doc, 'umra-err'));
});

test('server error on login shows the network message, not "wrong password"', async () => {
  const { doc } = await boot({ session: null, signIn: { error: { status: 503, message: 'down' } } });
  fill(doc, 'umra-email', 'x@y.uz'); fill(doc, 'umra-pass', 'p'); submit(doc, 'form'); await settle();
  assert.equal(text(doc, 'umra-err'), MSG.network);
});

test('viewer logs in: app is unlocked, logout bar shows the email', async () => {
  const { doc } = await boot({ session: null });
  fill(doc, 'umra-email', 'v@y.uz'); fill(doc, 'umra-pass', 'p'); submit(doc, 'form'); await settle();
  assert.ok(!locked(doc));
  assert.ok(!doc.getElementById('umra-auth'));
  assert.match(doc.getElementById('umra-auth-bar').textContent, /v@y\.uz/);
});

test('existing session of an active editor opens the app without 2FA', async () => {
  const { doc } = await boot({ session: SESSION, profile: { role: 'editor', active: true } });
  assert.ok(!locked(doc));
});

test('deactivated user is signed out, caches cleared, sees the message', async () => {
  const { doc, fake, win } = await boot({ session: SESSION, profile: { role: 'editor', active: false } });
  assert.ok(locked(doc));
  assert.ok(fake.calls.some((c) => c[0] === 'signOut'));
  assert.deepEqual(win.cachesDeleted.sort(), ['other', 'umra-v3']);
  assert.equal(text(doc, 'umra-err'), MSG.inactive);
});

test('session without a profile row is treated as inactive', async () => {
  const { doc, fake } = await boot({ session: SESSION, profile: null });
  assert.ok(locked(doc));
  assert.ok(fake.calls.some((c) => c[0] === 'signOut'));
});

test('admin without 2FA must enrol: QR + secret shown, app stays locked', async () => {
  const { doc, fake } = await boot({ session: SESSION, profile: { role: 'admin', active: true },
    factors: { totp: [], all: [{ id: 'old', status: 'unverified' }] } });
  assert.ok(locked(doc));
  assert.ok(doc.getElementById('umra-secret'));
  assert.ok(fake.calls.some((c) => c[0] === 'unenroll' && c[1] === 'old'), 'abandoned factor is removed first');
});

test('QR image cannot inject script into the page', async () => {
  const { doc } = await boot({ session: SESSION, profile: { role: 'admin', active: true } });
  assert.equal(doc.querySelectorAll('script').length, 0);
  const src = doc.querySelector('img.qr').getAttribute('src');
  assert.ok(src.startsWith('data:image/svg+xml;utf8,'));
  assert.ok(!src.includes('<script'), 'svg is URL-encoded inside an <img>');
});

test('admin enrols with a correct code -> app opens', async () => {
  const { doc, fake } = await boot({ session: SESSION, profile: { role: 'admin', active: true } });
  fill(doc, 'umra-code', '123456'); submit(doc, 'form'); await settle();
  assert.ok(fake.calls.some((c) => c[0] === 'verify' && c[1] === 'f-new' && c[2] === '123456'));
  assert.ok(!locked(doc));
});

test('user with a TOTP factor at aal1 gets the challenge; wrong code keeps it locked', async () => {
  const { doc } = await boot({ session: SESSION, profile: { role: 'admin', active: true },
    levels: { currentLevel: 'aal1', nextLevel: 'aal2' }, factors: { totp: [{ id: 'f1' }], all: [{ id: 'f1', status: 'verified' }] },
    verifyOk: false });
  assert.ok(locked(doc));
  fill(doc, 'umra-code', '000000'); submit(doc, 'form'); await settle();
  assert.equal(text(doc, 'umra-err'), MSG.badCode);
  assert.ok(locked(doc));
});

test('challenge with the right code opens the app', async () => {
  const { doc } = await boot({ session: SESSION, profile: { role: 'admin', active: true },
    levels: { currentLevel: 'aal1', nextLevel: 'aal2' }, factors: { totp: [{ id: 'f1' }], all: [] } });
  fill(doc, 'umra-code', '654321'); submit(doc, 'form'); await settle();
  assert.ok(!locked(doc));
});

test('code must be exactly 6 digits, nothing is sent otherwise', async () => {
  const { doc, fake } = await boot({ session: SESSION, profile: { role: 'admin', active: true } });
  fill(doc, 'umra-code', '12ab'); submit(doc, 'form'); await settle();
  assert.equal(text(doc, 'umra-err'), MSG.badCode);
  assert.ok(!fake.calls.some((c) => c[0] === 'verify'));
});

test('admin who already has 2FA (aal2) goes straight in', async () => {
  const { doc } = await boot({ session: SESSION, profile: { role: 'admin', active: true },
    levels: { currentLevel: 'aal2', nextLevel: 'aal2' } });
  assert.ok(!locked(doc));
});

test('logout: signs out, clears ALL caches, reloads', async () => {
  const { doc, fake, win } = await boot({ session: SESSION, profile: { role: 'viewer', active: true } });
  doc.getElementById('umra-logout').click(); await settle();
  assert.ok(fake.calls.some((c) => c[0] === 'signOut'));
  assert.deepEqual(win.cachesDeleted.sort(), ['other', 'umra-v3']);
  assert.ok(win.reloads >= 1);
});

test('signed out in another tab -> this tab reloads and locks', async () => {
  const { fake, win } = await boot({ session: SESSION, profile: { role: 'viewer', active: true } });
  fake.emit('SIGNED_OUT');
  assert.equal(win.reloads, 1);
});

test('periodic re-check is scheduled after unlock', async () => {
  const { win } = await boot({ session: SESSION, profile: { role: 'viewer', active: true } });
  assert.deepEqual(win.intervals, [5 * 60 * 1000]);
});

test('profile lookup failing with a server error does not open the app', async () => {
  const { doc } = await boot({ session: SESSION, profile: null, profileError: { status: 503 } });
  assert.ok(locked(doc));
});
