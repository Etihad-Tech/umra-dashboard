# tests-web

Tests for the login gate (`../auth.js`), the dashboard data and page logic (`../live.js`, `../index.html`), manual
entry (`../kiritish.js`), the service worker (`../sw.js`) and the deploy settings (`../deploy/`: CSP, headers,
containers, backup), with fake Supabase clients (no network).

```bash
cd tests-web
npm ci
npm test
```
