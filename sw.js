// Oddiy offline-kesh: ilova fayllari keshlanadi, yangi versiya chiqsa VERSION ni o'zgartiring.
const VERSION = 'umra-v6';
const CORE = [
  './', './index.html', './support.js', './manifest.webmanifest',
  './auth.js', './kiritish.html', './kiritish.js', './config.js', './vendor/supabase.js',
  './live.js', './etihad-meta.js', './data/services.js',
  './icons/icon-192.png', './icons/icon-512.png'
];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
// Tarmoq birinchi (yangi narxlar), ishlamasa kesh.
// Faqat o'z saytimiz fayllari keshlanadi: Supabase API javoblari (shaxsiy ma'lumot) hech qachon keshga tushmaydi.
const FONT_HOSTS = ['fonts.googleapis.com', 'fonts.gstatic.com'];
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  const same = url.origin === self.location.origin;
  if (!same && !FONT_HOSTS.includes(url.hostname)) return; // let the browser handle it, no caching
  e.respondWith(
    fetch(e.request).then((r) => {
      if (r.ok || r.type === 'opaque') {
        const copy = r.clone();
        caches.open(VERSION).then((c) => c.put(e.request, copy)).catch(() => {});
      }
      return r;
    }).catch(() => caches.match(e.request).then((m) => m || (same ? caches.match('./index.html') : undefined)))
  );
});
