# Auth setup (Phase 2)

Login is Supabase Auth (email + password). There is **no public sign-up**; people are created by an admin.

## 1. Project settings (Supabase dashboard -> Authentication)
| Setting | Where | Value |
|---|---|---|
| Allow new users to sign up | Sign In / Providers | **off** |
| Confirm email | Sign In / Providers | on |
| Minimum password length | Sign In / Providers -> Email | **10** or more |
| Leaked password protection | Sign In / Providers -> Email | on, if your plan has it |
| TOTP (authenticator app) | Multi-Factor | enabled |

## 2. Apply migration 5
SQL Editor -> paste `migrations/20261108000005_admin_requires_2fa.sql` -> Run.
After it, an `admin` only has admin rights in a session that passed 2FA (`aal2`). Without 2FA the same person acts as a viewer.

## 3. Create the first admin
1. Authentication -> Users -> **Add user -> Create new user**: email, strong password, tick **Auto Confirm User**.
   A `profiles` row is created automatically (role `viewer`, active).
2. SQL Editor:
   ```sql
   update public.profiles set role = 'admin'
   where user_id = (select id from auth.users where email = 'ADMIN@EXAMPLE.COM');
   ```
3. On first login the app asks this admin to set up 2FA (QR code -> authenticator app). It cannot be skipped.

## 4. Other people
Same as above, but choose the role: `viewer` (read), `editor` (read + manual entry), `admin`.
```sql
update public.profiles set role = 'editor'
where user_id = (select id from auth.users where email = 'PERSON@EXAMPLE.COM');
```
Lock someone out (effective at their next request, enforced by RLS):
```sql
update public.profiles set active = false
where user_id = (select id from auth.users where email = 'PERSON@EXAMPLE.COM');
```

## 5. Connect the web app
Fill `config.js` (Project URL and the **anon / publishable** key from Project Settings -> API).
Never put the `service_role` / secret key in the web app.

## Known limits (until Phase 6)
- The price data still ships as static files (`data/*.js`) and can be downloaded without logging in.
  The login screen hides the app, but real protection starts when the dashboard reads data from Supabase (Phase 6).
- The login check needs the network, so the app does not open offline.
