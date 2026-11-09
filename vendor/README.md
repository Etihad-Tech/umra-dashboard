# vendor/

`supabase.js` is `@supabase/supabase-js` 2.119.0 (UMD build, MIT license, see `supabase-js.LICENSE`).
It is stored here on purpose so the Supabase client is not loaded from a third-party CDN at runtime.
The page runtime (`support.js`, from the Claude Design template) still loads React 18.3.1, ReactDOM 18.3.1
and @babel/standalone 7.29.0 from cdn.jsdelivr.net, pinned with SRI hashes; the CSP in `deploy/Caddyfile`
allows exactly those three files and nothing else from that host.
To update: `npm pack @supabase/supabase-js@2`, copy `package/dist/umd/supabase.js`, bump the version above and the service-worker VERSION.
