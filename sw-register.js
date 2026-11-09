// Registers the service worker (offline app shell). A separate file, not an inline <script>, so the
// Content-Security-Policy can forbid inline scripts.
if ('serviceWorker' in navigator && location.protocol.indexOf('http') === 0) {
  window.addEventListener('load', function () { navigator.serviceWorker.register('./sw.js').catch(function () {}); });
}
