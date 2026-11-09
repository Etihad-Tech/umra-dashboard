-- Roles (viewer / editor / admin / deactivated) and the price override + audit rules.

begin;
\ir 00_fixture.sql

-- a departure id known to the tests (users below may not be able to SELECT it, so no INSERT ... SELECT)
select set_config('test.dep', (select min(id)::text from public.departures), true) \gset

-- ================================================================= viewer
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000b1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  select count(*) into n from public.packages;       assert n = 2, 'viewer must read packages';
  select count(*) into n from public.price_current;  assert n > 0, 'viewer must read price_current';
  select count(*) into n from public.audit_log;      assert n = 0, 'viewer must not read audit_log';
  select count(*) into n from public.app_settings;   assert n = 0, 'viewer must not read app_settings';
  select count(*) into n from public.profiles;       assert n = 1, 'viewer must see only their own profile';

  begin
    insert into public.manual_entries (departure_id, pax, price_usd) values (current_setting('test.dep')::bigint, 4, 1900);
    assert false, 'viewer inserted a manual entry';
  exception when insufficient_privilege then null;
  end;

  update public.profiles set role = 'admin' where user_id = '00000000-0000-0000-0000-0000000000b1';
  get diagnostics n = row_count;
  assert n = 0, 'viewer promoted themselves to admin';
end
$$;
reset role;

-- ================================================================= editor
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000e1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare
  n bigint;
  v_dep bigint;
  v_our bigint;
  v_their bigint;
begin
  select d.id into v_dep from public.departures d join public.packages p on p.id = d.package_id where p.company_id = 'F';

  insert into public.manual_entries (departure_id, pax, price_usd, note) values (v_dep, 4, 1900, 'site was down');
  select count(*) into n from public.manual_entries where created_by = '00000000-0000-0000-0000-0000000000e1';
  assert n = 1, 'editor must be able to add a manual entry (created_by defaults to themselves)';

  -- an entry in someone else's name is impossible: the server writes created_by (tested in 50_review_fixes)

  begin
    update public.manual_entries set price_usd = 1 + 100 where created_by = '00000000-0000-0000-0000-0000000000e1';
    assert false, 'editor changed the price of an existing manual entry';
  exception when raise_exception then null;
  end;

  update public.manual_entries set note = 'edited' where created_by = '00000000-0000-0000-0000-0000000000e1';
  get diagnostics n = row_count;
  assert n = 1, 'editor must be able to edit the note';

  select id into v_our   from public.packages where company_id = 'B';
  select id into v_their from public.packages where company_id = 'F';
  begin
    insert into public.package_matches (our_package_id, their_package_id) values (v_their, v_our);
    assert false, 'reversed package match accepted';
  exception when raise_exception then null;
  end;

  begin
    insert into public.sources (company_id, kind) values ('B', 'manual');
    assert false, 'editor created a source';
  exception when insufficient_privilege then null;
  end;

  select count(*) into n from public.audit_log;  assert n = 0, 'editor must not read audit_log';
end
$$;
reset role;

-- ================================================================== admin
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated","aal":"aal2"}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  -- the editor's insert and update above were recorded by the triggers
  select count(*) into n from public.audit_log where actor = '00000000-0000-0000-0000-0000000000e1';
  assert n >= 2, format('audit_log should hold the editor''s changes, found %s', n);

  select count(*) into n from public.app_settings;  assert n = 1, 'admin must read app_settings';
  select count(*) into n from public.profiles;      assert n = 4, 'admin must see all profiles';

  update public.profiles set role = 'editor' where user_id = '00000000-0000-0000-0000-0000000000b1';
  get diagnostics n = row_count;
  assert n = 1, 'admin must be able to change a role';

  insert into public.sources (company_id, kind) values ('B', 'manual');   -- admin manages sources
end
$$;
reset role;

-- ==================================================== deactivated user
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000f1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  select count(*) into n from public.packages;        assert n = 0, 'deactivated user must see no packages';
  select count(*) into n from public.manual_entries;  assert n = 0, 'deactivated user must see no manual entries';
  select count(*) into n from public.events;          assert n = 0, 'deactivated user must see no events';
  begin
    insert into public.manual_entries (departure_id, pax, price_usd) values (current_setting('test.dep')::bigint, 4, 1900);
    assert false, 'deactivated editor inserted a manual entry';
  exception when insufficient_privilege then null;
  end;
end
$$;
reset role;

-- ========================================== audit_log is immutable for everyone
do $$
begin
  begin update public.audit_log set action = 'x';  assert false, 'audit_log was updated';   exception when raise_exception then null; end;
  begin delete from public.audit_log;              assert false, 'audit_log was deleted';   exception when raise_exception then null; end;
  begin truncate public.audit_log;                 assert false, 'audit_log was truncated'; exception when raise_exception then null; end;
end
$$;

-- ================================================== manual entry -> event
do $$
declare n bigint;
begin
  select count(*) into n from public.events where type = 'manual_entry' and is_manual;
  assert n >= 2, format('manual entries must create events, found %s', n);
end
$$;

-- ===================================== price_current: override and conflict
do $$
declare
  v_dep bigint;
  r record;
begin
  select d.id into v_dep from public.departures d join public.packages p on p.id = d.package_id where p.company_id = 'F';

  -- missing price stays NULL, never 0
  select * into r from public.price_current where departure_id = v_dep and pax = 1;
  assert r.price_usd is null and not r.is_manual, 'missing price must stay NULL';

  -- the editor's manual 1900 (pax 4) overrides the automatic 2000
  select * into r from public.price_current where departure_id = v_dep and pax = 4;
  assert r.price_usd = 1900 and r.is_manual and r.auto_price_usd = 2000 and not r.conflict,
         'manual entry must override the automatic price without a conflict';

  -- the source later reports a DIFFERENT value: both are shown, conflict is flagged, manual still wins
  insert into public.price_snapshots (departure_id, pax, price_usd, seen_at) values (v_dep, 4, 1950, now() + interval '1 minute');
  select * into r from public.price_current where departure_id = v_dep and pax = 4;
  assert r.price_usd = 1900 and r.auto_price_usd = 1950 and r.conflict, 'a differing automatic price must raise a conflict';

  -- clearing the manual entry returns control to the automatic price
  update public.manual_entries set cleared_at = now() where departure_id = v_dep and pax = 4;
  select * into r from public.price_current where departure_id = v_dep and pax = 4;
  assert r.price_usd = 1950 and not r.is_manual, 'cleared manual entry must stop overriding';
end
$$;

-- ============================== collector (service role) can read and write
set local role service_role;
do $$
declare n bigint;
begin
  select count(*) into n from public.packages;
  assert n = 2, 'service_role must read business tables';
  insert into public.events (type, company_id) values ('source_down', 'F');
  update public.sources set last_ok_at = now(), consecutive_failures = 0 where company_id = 'F';
  insert into public.price_snapshots (departure_id, pax, price_usd)
  select id, 3, 1700 from public.departures limit 1;
end
$$;
reset role;

rollback;
\echo 'OK  20_roles_and_logic'
