# Auth setup

Login is Supabase Auth (email + password). There is **no public sign-up**; people are created by an admin.
A new account starts **closed** (inactive viewer): it sees nothing until an admin activates it.

## 1. Project settings (Supabase dashboard -> Authentication)
| Setting | Where | Value |
|---|---|---|
| Allow new users to sign up | Sign In / Providers | **off** |
| Allow anonymous sign-ins | Sign In / Providers | **off** (must stay off) |
| Other providers (Google, GitHub, phone, ...) | Sign In / Providers | **off** |
| Confirm email | Sign In / Providers | on |
| Minimum password length | Sign In / Providers -> Email | **10** or more |
| Leaked password protection | Sign In / Providers -> Email | on, if your plan has it |
| TOTP (authenticator app) | Multi-Factor | enabled |

If sign-up or anonymous sign-ins were ever switched on by mistake, the database still protects the data: such an
account gets an inactive profile (no rows), and an anonymous session never counts as an active user (migration 10).

## 2. Apply the migrations
SQL Editor -> paste each file of `migrations/` in filename order that is not applied yet -> Run.
- Migration 5: an `admin` only has admin rights in a session that passed 2FA (`aal2`); without 2FA the same person acts as a viewer.
- Migration 10: new accounts start inactive; nobody can be made admin without a verified 2FA factor; who/when on a
  manual price is set by the server; changes made in the SQL editor are audited too.
- Migration 11: same rules, faster (policies evaluated once per query).

## 3. Add a person (invite -> admin activates)
1. Authentication -> Users -> **Add user -> Create new user** (email, strong password, tick **Auto Confirm User**),
   or **Invite user**. A `profiles` row is created automatically: role `viewer`, **inactive**.
2. SQL Editor — open the account and choose the role, `viewer` (read) or `editor` (read + manual entry):
   ```sql
   update public.profiles set active = true, role = 'viewer'   -- or 'editor'
   where user_id = (select id from auth.users where email = 'PERSON@EXAMPLE.COM');
   ```
   The person can log in now. Until this step they see "Hisobingiz faol emas" (account not active).

## 4. Make someone admin (2FA first, then the role)
The database refuses `role = 'admin'` for a user without a **verified** TOTP factor. This stops whoever logs in
first with a new admin's password from setting up *their* authenticator and owning the account.
1. Add and activate the person as a viewer (section 3).
2. The person logs in, presses **2FA** in the top bar, scans the QR code with Google Authenticator (or similar)
   and enters the 6-digit code. The button disappears when 2FA is on.
3. SQL Editor:
   ```sql
   update public.profiles set role = 'admin'
   where user_id = (select id from auth.users where email = 'ADMIN@EXAMPLE.COM');
   ```
   If it fails with "has no verified 2FA (TOTP) factor", step 2 was not finished.

The very first admin is made the same way (sections 3 and 4).

## 5. Lock someone out
Effective at their next request, enforced by RLS:
```sql
update public.profiles set active = false
where user_id = (select id from auth.users where email = 'PERSON@EXAMPLE.COM');
```
Every change to `profiles` — from the app or from the SQL editor — is written to `audit_log`
(`actor` = the person in the app, empty for the SQL editor; `db_role` = the database role, e.g. `postgres`).

## 6. Connect the web app
Fill `config.js` (Project URL and the **anon / publishable** key from Project Settings -> API).
Never put the `service_role` / secret key in the web app.

## Known limits
- The login check needs the network, so the app does not open offline (the service worker never caches API data).
- A locked-out person keeps a valid login token until it expires, but the database returns no data to it at once.
- Roles and activation are changed in the SQL editor; there is no admin screen for people yet.
