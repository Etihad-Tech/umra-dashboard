-- Phase 2 / migration 5: admin rights are only effective in a 2FA (aal2) session.
-- A stolen or guessed password alone (aal1 token) gives an admin the rights of a viewer.
-- The admin can still log in, reach the 2FA screen and enrol/verify a TOTP factor: that uses the Auth API, not RLS.
-- The service role and the SQL console are not affected (they have no auth.uid()).

create or replace function app.user_role() returns text
language sql stable security definer set search_path = ''
as $$
  select case
           when p.role = 'admin' and coalesce(auth.jwt() ->> 'aal', 'aal1') <> 'aal2' then 'viewer'
           else p.role
         end
    from public.profiles p
   where p.user_id = auth.uid() and p.active
$$;
