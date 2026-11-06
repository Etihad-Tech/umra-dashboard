// Oddiy offline-kesh: ilova fayllari keshlanadi, yangi versiya chiqsa VERSION ni o'zgartiring.
const VERSION = 'umra-v1';
const CORE = [
  './', './index.html', './support.js', './manifest.webmanifest',
  './data/info.js', './data/packages.js', './data/services.js', './data/competitors.js',
  './data/prices.js', './data/ours.js', './data/events.js',
  './icons/icon-192.png', './icons/icon-512.png'
];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
// Tarmoq birinchi (yangi narxlar), ishlamasa kesh.
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).then((r) => {
      const copy = r.clone();
      caches.open(VERSION).then((c) => c.put(e.request, copy)).catch(() => {});
      return r;
    }).catch(() => caches.match(e.request).then((m) => m || caches.match('./index.html')))
  );
});
