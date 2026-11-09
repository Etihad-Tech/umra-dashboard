-- Review fixes (migrations 10 and 11): server-side stamps, closed new accounts, admin needs 2FA,
-- complete audit log, RLS helpers evaluated once, future tables closed.

begin;
\ir 00_fixture.sql

select set_config('test.dep', (select min(id)::text from public.departures), true) \gset

-- ===================================================== 1. forged stamps (editor)
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000e1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare
  r record;
  v_dep bigint := current_setting('test.dep')::bigint;
  v_our bigint;
  v_new bigint;
  v_match bigint;
begin
  -- created_by / created_at / valid_until from the browser are not trusted
  insert into public.manual_entries (departure_id, pax, price_usd, created_by, created_at, valid_until)
  values (v_dep, 3, 1500, '00000000-0000-0000-0000-0000000000a1', now() + interval '100 years', 'infinity')
  returning * into r;
  assert r.created_by = '00000000-0000-0000-0000-0000000000e1', format('created_by was forged: %s', r.created_by);
  assert r.created_at = now(), format('created_at was forged: %s', r.created_at);
  assert r.valid_until = now() + interval '30 days', format('valid_until must be capped at 30 days, got %s', r.valid_until);

  -- not given (or NULL) -> 7 days
  insert into public.manual_entries (departure_id, pax, price_usd) values (v_dep, 1, 1600) returning * into r;
  assert r.valid_until = now() + interval '7 days', format('default validity must be 7 days, got %s', r.valid_until);
  insert into public.manual_entries (departure_id, pax, price_usd, valid_until) values (v_dep, 1, 1601, null) returning * into r;
  assert r.valid_until = now() + interval '7 days', 'NULL validity must become 7 days';

  -- the app's "30 days" is computed on the user's clock and may be a few seconds over: capped, not refused
  insert into public.manual_entries (departure_id, pax, price_usd, valid_until)
  values (v_dep, 2, 1602, now() + interval '30 days 5 seconds') returning * into r;
  assert r.valid_until = now() + interval '30 days', 'a slightly-over 30 days must be capped';

  -- extending someone else's entry for ever: capped
  update public.manual_entries set valid_until = 'infinity' where created_by = '00000000-0000-0000-0000-0000000000a1';
  select * into r from public.manual_entries where created_by = '00000000-0000-0000-0000-0000000000a1' limit 1;
  assert r.valid_until = now() + interval '30 days', format('an update must be capped too, got %s', r.valid_until);

  -- the stamps of an existing entry cannot be rewritten
  begin
    update public.manual_entries set created_by = '00000000-0000-0000-0000-0000000000b1'
     where created_by = '00000000-0000-0000-0000-0000000000e1';
    assert false, 'created_by of an entry was changed';
  exception when raise_exception then null;
  end;
  begin
    update public.manual_entries set created_at = now() + interval '1 year'
     where created_by = '00000000-0000-0000-0000-0000000000e1';
    assert false, 'created_at of an entry was changed';
  exception when raise_exception then null;
  end;

  -- package matches: stamps forced on insert ...
  select id into v_our from public.packages where company_id = 'B';
  insert into public.packages (company_id, external_key, is_manual) values ('S', 'MANUAL-S1', true) returning id into v_new;
  insert into public.package_matches (our_package_id, their_package_id, created_by, created_at)
  values (v_our, v_new, '00000000-0000-0000-0000-0000000000b1', '2000-01-01')
  returning * into r;
  assert r.created_by = '00000000-0000-0000-0000-0000000000e1' and r.created_at = now(),
         format('package match stamps were forged: %s %s', r.created_by, r.created_at);
  v_match := r.id;

  -- ... and an update may only touch note / active
  update public.package_matches set note = 'checked', active = false where id = v_match;
  begin
    update public.package_matches set created_by = '00000000-0000-0000-0000-0000000000b1' where id = v_match;
    assert false, 'editor re-attributed a package match';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.package_matches set created_at = '2000-01-01' where id = v_match;
    assert false, 'editor back-dated a package match';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.package_matches set their_package_id = v_our where id = v_match;
    assert false, 'editor rewired a package match';
  exception when insufficient_privilege then null;
  end;
end
$$;
reset role;

-- the price_current view no longer hides a conflict behind a future created_at: the newest entry is from now()
do $$
declare r record; v_dep bigint := current_setting('test.dep')::bigint;
begin
  insert into public.price_snapshots (departure_id, pax, price_usd, seen_at) values (v_dep, 3, 1999, now() + interval '1 minute');
  select * into r from public.price_current where departure_id = v_dep and pax = 3;
  assert r.price_usd = 1500 and r.auto_price_usd = 1999 and r.conflict, 'a differing automatic price must show a conflict';
end
$$;

-- without a uid (SQL editor / restore) the given stamps are kept, so a backup restores as it was
select set_config('request.jwt.claims', '', true) \gset
do $$
declare r record;
begin
  insert into public.manual_entries (departure_id, pax, price_usd, created_by, created_at, valid_until)
  values (current_setting('test.dep')::bigint, 4, 1700, '00000000-0000-0000-0000-0000000000a1',
          now() - interval '20 days', now() - interval '13 days')
  returning * into r;
  assert r.created_at = now() - interval '20 days' and r.valid_until = now() - interval '13 days',
         'SQL-path stamps must be kept';
end
$$;

-- ========================================== 2. an admin without 2FA (aal1) cannot write
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated","aal":"aal1"}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  begin
    insert into public.manual_entries (departure_id, pax, price_usd) values (current_setting('test.dep')::bigint, 4, 1900);
    assert false, 'aal1 admin added a manual price';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.packages (company_id, external_key, is_manual) values ('F', 'AAL1-1', true);
    assert false, 'aal1 admin added a package';
  exception when insufficient_privilege then null;
  end;
  update public.app_settings set value = '{"chat_id": "evil"}' where key = 'telegram';
  get diagnostics n = row_count;
  assert n = 0, 'aal1 admin changed app_settings';
  update public.manual_entries set cleared_at = now();
  get diagnostics n = row_count;
  assert n = 0, 'aal1 admin cleared manual entries';
end
$$;
reset role;

-- ========================================== 3. new accounts start closed
insert into auth.users (id, email, raw_user_meta_data) values
  ('00000000-0000-0000-0000-0000000000c1', 'new@test', '{"full_name": "New Person"}');
insert into auth.users (id, raw_user_meta_data, is_anonymous) values
  ('00000000-0000-0000-0000-0000000000d1', '{"full_name": "Boss"}', true);

do $$
declare r record;
begin
  select * into r from public.profiles where user_id = '00000000-0000-0000-0000-0000000000c1';
  assert r.role = 'viewer' and not r.active and r.full_name = 'New Person',
         format('a new user must be an inactive viewer, got %s %s', r.role, r.active);
  select * into r from public.profiles where user_id = '00000000-0000-0000-0000-0000000000d1';
  assert not r.active and r.full_name = '', 'an anonymous user must be inactive with an empty name';
  assert not exists (select 1 from public.profile_names
                      where user_id in ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000d1')),
         'closed accounts must not appear in profile_names';
end
$$;

-- the new (not yet activated) user reads nothing but their own profile row
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000c1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  select count(*) into n from public.packages;       assert n = 0, 'new user must see no packages before activation';
  select count(*) into n from public.price_current;  assert n = 0, 'new user must see no prices before activation';
  select count(*) into n from public.profiles;       assert n = 1, 'new user sees only their own (inactive) profile';
end
$$;
reset role;

-- an anonymous session reads 0 rows, even if someone activated its profile by mistake
update public.profiles set active = true where user_id = '00000000-0000-0000-0000-0000000000d1';
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000d1","role":"authenticated","is_anonymous":true}', true) \gset
set local role authenticated;
do $$
declare
  r record;
  n bigint;
begin
  for r in
    select c.relname from pg_class c join pg_namespace s on s.oid = c.relnamespace
     where s.nspname = 'public' and c.relkind in ('r', 'v') and c.relname not in ('profiles')
  loop
    execute format('select count(*) from public.%I', r.relname) into n;
    assert n = 0, format('anonymous user sees %s rows in public.%s', n, r.relname);
  end loop;
  begin
    insert into public.manual_entries (departure_id, pax, price_usd) values (current_setting('test.dep')::bigint, 4, 1);
    assert false, 'anonymous user wrote a manual entry';
  exception when insufficient_privilege then null;
  end;
end
$$;
reset role;

-- ========================================== 4. nobody becomes admin without a verified 2FA factor
do $$
begin
  -- SQL editor (no uid), no factor at all
  begin
    update public.profiles set role = 'admin' where user_id = '00000000-0000-0000-0000-0000000000b1';
    assert false, 'promoted to admin without a 2FA factor';
  exception when raise_exception then null;
  end;
  -- an unverified (abandoned) factor is not enough
  insert into auth.mfa_factors (id, user_id, friendly_name, factor_type, status, created_at, updated_at)
  values (gen_random_uuid(), '00000000-0000-0000-0000-0000000000b1', 'half', 'totp', 'unverified', now(), now());
  begin
    update public.profiles set role = 'admin' where user_id = '00000000-0000-0000-0000-0000000000b1';
    assert false, 'promoted to admin with only an unverified factor';
  exception when raise_exception then null;
  end;
  -- a profile cannot be created as admin either
  delete from public.profiles where user_id = '00000000-0000-0000-0000-0000000000c1';
  begin
    insert into public.profiles (user_id, role, active) values ('00000000-0000-0000-0000-0000000000c1', 'admin', true);
    assert false, 'profile inserted as admin without a factor';
  exception when raise_exception then null;
  end;
end
$$;

-- an admin in the app (aal2) cannot do it either
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated","aal":"aal2"}', true) \gset
set local role authenticated;
do $$
begin
  begin
    update public.profiles set role = 'admin' where user_id = '00000000-0000-0000-0000-0000000000b1';
    assert false, 'admin promoted a user without 2FA';
  exception when raise_exception then null;
  end;
end
$$;
reset role;

-- with a verified factor the promotion works; other changes to an existing admin are not blocked
do $$
declare n bigint;
begin
  update auth.mfa_factors set status = 'verified' where user_id = '00000000-0000-0000-0000-0000000000b1';
  update public.profiles set role = 'admin' where user_id = '00000000-0000-0000-0000-0000000000b1';
  get diagnostics n = row_count;
  assert n = 1, 'promotion with a verified factor must work';
  update public.profiles set full_name = 'Renamed admin' where user_id = '00000000-0000-0000-0000-0000000000a1';
  update public.profiles set role = 'viewer' where user_id = '00000000-0000-0000-0000-0000000000b1';
end
$$;

-- ========================================== 5. audit log is complete (except the collector)
select set_config('request.jwt.claims', '', true) \gset
do $$
declare
  r record;
  n bigint;
begin
  -- a SQL-editor style change (no JWT, no SET ROLE) is logged with the database role
  update public.profiles set role = 'editor' where user_id = '00000000-0000-0000-0000-0000000000b1';
  select * into r from public.audit_log
   where table_name = 'profiles' and row_id = '00000000-0000-0000-0000-0000000000b1' and after ->> 'role' = 'editor'
   order by id desc limit 1;
  assert found, 'a SQL-editor role change must be in the audit log';
  assert r.actor is null and r.db_role = session_user and r.before ->> 'role' = 'viewer',
         format('audit row must show no actor, db_role %s and the old role, got %s / %s', session_user, r.actor, r.db_role);

  -- changes made in the app carry the person and the role 'authenticated'
  select count(*) into n from public.audit_log
   where actor = '00000000-0000-0000-0000-0000000000e1' and db_role = 'authenticated' and table_name = 'manual_entries';
  assert n >= 3, format('editor''s app changes must be logged as authenticated, found %s', n);

  -- an Auth-created profile (new user) is logged too
  select count(*) into n from public.audit_log
   where table_name = 'profiles' and action = 'INSERT' and row_id = '00000000-0000-0000-0000-0000000000d1';
  assert n = 1, 'a profile created for a new auth user must be logged';
end
$$;

set local role service_role;
do $$
begin
  update public.sources set last_ok_at = now() where company_id = 'F';
  insert into public.packages (company_id, external_key) values ('F', 'SVC-1');
end
$$;
reset role;
do $$
declare n bigint;
begin
  select count(*) into n from public.audit_log where db_role = 'service_role';
  assert n = 0, format('collector (service_role) writes must not be logged, found %s', n);
end
$$;

-- ========================================== 6. RLS helpers are evaluated once per query
do $$
declare
  r record;
  e text;
  n int := 0;
begin
  for r in select tablename, policyname, coalesce(qual, '') || ' ' || coalesce(with_check, '') as expr
             from pg_policies where schemaname = 'public'
  loop
    n := n + 1;
    -- every app.* / auth.* call must sit inside its own sub-select ("( SELECT app.fn(...) AS ...)")
    e := regexp_replace(r.expr, '\(\s*SELECT (app|auth)\.[a-z_]+\(', '(SELECT wrapped(', 'g');
    assert e !~ '(app|auth)\.[a-z_]+\(', format('policy %s on %s calls a helper per row: %s', r.policyname, r.tablename, r.expr);
  end loop;
  assert n >= 25, format('expected at least the 25 policies of migrations 3/6/7, found %s', n);
end
$$;

select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000b1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare
  line text;
  plan text := '';
begin
  for line in execute 'explain select count(*) from public.price_snapshots' loop
    plan := plan || line || E'\n';
  end loop;
  assert plan ~ 'InitPlan', format('the active-user check must be an InitPlan:%s%s', E'\n', plan);
  assert plan !~ 'Filter: app\.', format('the active-user check must not run per row:%s%s', E'\n', plan);
end
$$;
reset role;

-- ========================================== 7. a table created later is closed by default
do $$
begin
  create table public.probe_future_table (id int);
  assert not has_table_privilege('authenticated', 'public.probe_future_table', 'select'), 'authenticated can read a new table';
  assert not has_table_privilege('authenticated', 'public.probe_future_table', 'insert'), 'authenticated can write a new table';
  assert not has_table_privilege('anon', 'public.probe_future_table', 'select'), 'anon can read a new table';
  assert has_table_privilege('service_role', 'public.probe_future_table', 'select'), 'service_role must keep access';
  drop table public.probe_future_table;
  assert not exists (
    select 1 from pg_default_acl d, aclexplode(d.defaclacl) a
     where d.defaclnamespace = 'public'::regnamespace
       and d.defaclrole in (select oid from pg_roles where rolname in (current_user, 'postgres'))
       and d.defaclobjtype in ('r', 'S')            -- tables and sequences
       and a.grantee in ('anon'::regrole, 'authenticated'::regrole)),
    'a default privilege still grants anon/authenticated a table or sequence in public';
end
$$;

rollback;
\echo 'OK  50_review_fixes'
