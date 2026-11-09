-- Review fixes / migration 10.
--   1. Who and when on a manual price or a package match is decided by the server, not by the browser;
--      a manual price is valid for at most 30 days.
--   2. A new account starts CLOSED (inactive viewer). An admin opens it. Anonymous sign-ins never get data.
--   3. Nobody becomes admin without a verified TOTP (2FA) factor.
--   4. The audit log also records changes made outside the app (SQL editor, dashboard), with the database role.
--      Only the collector (service_role) is not logged: it writes all the time and is not a person.
--   5. Tables created later in public are NOT readable by anon / authenticated until a migration grants it.

-- ------------------------------------------------- 1. stamps cannot be forged
-- A logged-in person (auth.uid() set) can no longer choose created_by / created_at.
-- Without a uid (SQL editor, a restore) the values given are kept, so history can be restored as it was.
-- valid_until: 7 days when not given, never more than 30 days ahead (the app offers 7 / 14 / 30 days).
create function app.manual_entries_stamp() returns trigger
language plpgsql set search_path = ''
as $$
declare
  v_uid uuid := auth.uid();
  v_cap timestamptz := now() + interval '30 days';
begin
  if tg_op = 'INSERT' then
    if v_uid is not null then
      new.created_by := v_uid;
      new.created_at := now();
    end if;
    new.valid_until := least(coalesce(new.valid_until, now() + interval '7 days'), v_cap);
  else
    -- manual_entries_guard (runs first, alphabetical order) already refuses changes to these; keep them anyway
    new.created_by := old.created_by;
    new.created_at := old.created_at;
    if new.valid_until is distinct from old.valid_until then
      new.valid_until := least(coalesce(new.valid_until, now() + interval '7 days'), v_cap);
    end if;
  end if;
  return new;
end
$$;

create trigger manual_entries_stamp
  before insert or update on public.manual_entries
  for each row execute function app.manual_entries_stamp();

create function app.package_matches_stamp() returns trigger
language plpgsql set search_path = ''
as $$
declare
  v_uid uuid := auth.uid();
begin
  if tg_op = 'INSERT' then
    if v_uid is not null then
      new.created_by := v_uid;
      new.created_at := now();
    end if;
  else
    new.created_by := old.created_by;
    new.created_at := old.created_at;
  end if;
  return new;
end
$$;

create trigger package_matches_stamp
  before insert or update on public.package_matches
  for each row execute function app.package_matches_stamp();

-- Editors only add a note to a match or switch it off ("unmatch"); the pair itself is not edited.
revoke update on public.package_matches from authenticated;
grant update (note, active) on public.package_matches to authenticated;

-- --------------------------------------------- 2. new accounts start closed
alter table public.profiles alter column active set default false;

-- Sign-up is off, but if it (or "anonymous sign-ins") were ever switched on by mistake, a stranger would only get
-- an inactive profile: no data until an admin activates it. Anonymous users also get no name from their metadata.
-- is_anonymous is read through to_jsonb so the function also works where auth.users has no such column.
create or replace function app.handle_new_user() returns trigger
language plpgsql security definer set search_path = ''
as $$
declare
  v_anonymous boolean := coalesce((to_jsonb(new) ->> 'is_anonymous')::boolean, false);
begin
  insert into public.profiles (user_id, full_name, active)
  values (
    new.id,
    case when v_anonymous then '' else coalesce(new.raw_user_meta_data ->> 'full_name', '') end,
    false
  )
  on conflict (user_id) do nothing;
  return new;
end
$$;

-- An anonymous session never counts as an active user, even if its profile were activated by mistake.
create or replace function app.is_active_user() returns boolean
language sql stable security definer set search_path = ''
as $$
  select coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false) = false
     and exists (select 1 from public.profiles p where p.user_id = auth.uid() and p.active)
$$;

create or replace function app.user_role() returns text
language sql stable security definer set search_path = ''
as $$
  select case
           when p.role = 'admin' and coalesce(auth.jwt() ->> 'aal', 'aal1') <> 'aal2' then 'viewer'
           else p.role
         end
    from public.profiles p
   where p.user_id = auth.uid() and p.active
     and coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false) = false
$$;

-- ------------------------------------------------ 3. admin needs 2FA first
-- Otherwise whoever logs in first with a new admin's password could enrol THEIR authenticator and own the account.
-- Order: the person logs in as a viewer, enrols 2FA (button "2FA" in the top bar), THEN gets the admin role.
create function app.profiles_admin_requires_2fa() returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  if new.role = 'admin'
     and (tg_op = 'INSERT' or old.role is distinct from 'admin' or old.user_id is distinct from new.user_id)
     and not exists (select 1 from auth.mfa_factors f
                      where f.user_id = new.user_id and f.factor_type = 'totp' and f.status = 'verified') then
    raise exception 'profiles: user % has no verified 2FA (TOTP) factor, so they cannot be made admin', new.user_id
      using hint = 'Let the person log in, press "2FA" in the top bar and finish the setup, then try again.';
  end if;
  return new;
end
$$;

create trigger profiles_admin_requires_2fa
  before insert or update of role, user_id on public.profiles
  for each row execute function app.profiles_admin_requires_2fa();

-- ---------------------------------------------------- 4. complete audit log
-- db_role = the database role that made the change: 'authenticated' (the app), 'postgres' (SQL editor),
-- 'supabase_auth_admin' (Auth creating a profile), ... Rows written before this migration have NULL here.
alter table public.audit_log add column db_role text;

-- Inside a SECURITY DEFINER function current_user is the owner, so the caller's role is read from the "role"
-- setting (what SET ROLE / PostgREST set), falling back to the login role.
create or replace function app.audit_row() returns trigger
language plpgsql security definer set search_path = ''
as $$
declare
  v_role text := coalesce(nullif(current_setting('role', true), 'none'), session_user);
  v_old  jsonb := case when tg_op in ('UPDATE', 'DELETE') then to_jsonb(old) end;
  v_new  jsonb := case when tg_op in ('INSERT', 'UPDATE') then to_jsonb(new) end;
begin
  if v_role = 'service_role' then
    return coalesce(new, old);         -- the collector / notifier: not a person, and it writes every few minutes
  end if;
  insert into public.audit_log (actor, db_role, action, table_name, row_id, before, after)
  values (
    auth.uid(), v_role, tg_op, tg_table_name,
    coalesce(v_new ->> 'id', v_old ->> 'id', v_new ->> 'user_id', v_old ->> 'user_id', v_new ->> 'key', v_old ->> 'key'),
    v_old, v_new
  );
  return coalesce(new, old);
end
$$;

-- ------------------------------------------ 5. future tables are closed too
-- Supabase grants ALL on every NEW public table to anon and authenticated by default. Migration 3 removed that
-- for anon only. From now on a new table is reachable by nobody but its owner and service_role until a migration
-- grants it explicitly. Existing tables keep their grants.
do $$
declare
  r text;
begin
  for r in
    select distinct rolname from pg_roles
     where rolname in (current_user, 'postgres') and pg_has_role(current_user, oid, 'MEMBER')
  loop
    execute format('alter default privileges for role %I in schema public revoke all on tables    from anon, authenticated', r);
    execute format('alter default privileges for role %I in schema public revoke all on sequences from anon, authenticated', r);
  end loop;
end
$$;

-- ------------------------------------------- existing over-long manual prices
-- Entries already stored with a validity longer than 30 days are brought back to the limit (logged by audit_row).
-- A row with a created_at in the FUTURE is left alone (capping it would break "valid_until > created_at");
-- find such rows with:  select * from public.manual_entries where created_at > now();
update public.manual_entries
   set valid_until = now() + interval '30 days'
 where valid_until > now() + interval '30 days'
   and created_at < now();

-- ------------------------------------------------------------------- grants
revoke all on function app.manual_entries_stamp(), app.package_matches_stamp(), app.profiles_admin_requires_2fa()
  from public, anon, authenticated;
