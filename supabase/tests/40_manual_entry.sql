-- Phase 5: editors add manual packages/dates; automatic rows stay protected; audit; names.

begin;
\ir 00_fixture.sql

-- the fixture's automatic package and departure (made as the migration owner)
select set_config('test.pkg', (select id::text from public.packages where company_id = 'F'), true) \gset
select set_config('test.dep', (select min(id)::text from public.departures), true) \gset

-- ================================================== viewer: nothing
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000b1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
begin
  begin
    insert into public.packages (company_id, external_key, is_manual) values ('F', 'V-1', true);
    assert false, 'viewer must not add packages';
  exception when insufficient_privilege or check_violation then null;
  end;
end
$$;
reset role;

-- ================================================== editor
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000e1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare
  n bigint;
  v_pkg bigint;
  v_dep bigint;
begin
  -- adds a manual package and a date
  insert into public.packages (company_id, external_key, name, tier, nights, is_manual)
  values ('F', 'MANUAL-1', 'Manual package', 'lux', 9, true) returning id into v_pkg;
  insert into public.departures (package_id, date, is_manual) values (v_pkg, date '2027-01-10', true)
  returning id into v_dep;

  -- adds a missing date to an AUTOMATIC package (allowed: only the new row is manual)
  insert into public.departures (package_id, date, is_manual)
  values (current_setting('test.pkg')::bigint, date '2027-02-02', true);

  -- may NOT add rows that pretend to be automatic
  begin
    insert into public.packages (company_id, external_key, is_manual) values ('F', 'FAKE-AUTO', false);
    assert false, 'editor must not insert a non-manual package';
  exception when insufficient_privilege or check_violation then null;
  end;
  begin
    insert into public.departures (package_id, date) values (v_pkg, date '2027-03-03');
    assert false, 'editor must not insert a non-manual departure';
  exception when insufficient_privilege or check_violation then null;
  end;

  -- may change their manual rows (name, hide), but not the columns that identify them
  update public.packages set name = 'Renamed', active = false where id = v_pkg;
  get diagnostics n = row_count; assert n = 1, 'editor edits a manual package';
  begin
    update public.packages set company_id = 'S' where id = v_pkg;
    assert false, 'company_id must not be editable';
  exception when insufficient_privilege then null;
  end;
  begin
    update public.packages set is_manual = false where id = v_pkg;
    assert false, 'is_manual must not be editable';
  exception when insufficient_privilege then null;
  end;

  -- must NOT touch automatic rows
  update public.packages set name = 'hacked' where id = current_setting('test.pkg')::bigint;
  get diagnostics n = row_count; assert n = 0, 'editor must not edit an automatic package';
  update public.departures set active = false where id = current_setting('test.dep')::bigint;
  get diagnostics n = row_count; assert n = 0, 'editor must not edit an automatic departure';
  begin
    delete from public.packages where id = current_setting('test.pkg')::bigint;
    get diagnostics n = row_count;
    assert n = 0, 'editor must not delete packages';
  exception when insufficient_privilege then null;   -- no DELETE grant at all: also correct
  end;
end
$$;

-- the audit log recorded the manual package and date, by the editor
reset role;
do $$
declare n bigint;
begin
  select count(*) into n from public.audit_log
   where actor = '00000000-0000-0000-0000-0000000000e1' and table_name in ('packages', 'departures');
  assert n >= 4, format('audit_log should hold the editor''s package/departure changes, found %s', n);
end
$$;

-- ================================================== names for the badge
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000e1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare n bigint; v text;
begin
  select count(*) into n from public.profile_names;
  assert n = 3, format('active user sees names of the 3 active users, got %s', n);
  select display_name into v from public.profile_names where user_id = '00000000-0000-0000-0000-0000000000a1';
  assert v = 'admin', format('display name falls back to the email prefix, got %s', v);
  select count(*) into n from public.profile_names where user_id = '00000000-0000-0000-0000-0000000000f1';
  assert n = 0, 'a deactivated person is not listed';
end
$$;
reset role;

-- a deactivated user and anon see nothing
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000f1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$ declare n bigint; begin select count(*) into n from public.profile_names; assert n = 0, 'inactive user sees no names'; end $$;
reset role;

set local role anon;
do $$
begin
  begin
    perform 1 from public.profile_names;
    assert false, 'anon must not read profile_names';
  exception when insufficient_privilege then null;
  end;
end
$$;
reset role;

-- ================================================== collector (service role) is unaffected
-- service role requests carry no 'sub', so auth.uid() is null
select set_config('request.jwt.claims', '{"role":"service_role"}', true) \gset
set local role service_role;
do $$
declare n bigint;
begin
  insert into public.packages (company_id, external_key) values ('F', 'AUTO-1');
  select count(*) into n from public.packages where is_manual;
  assert n >= 1, 'service role sees manual packages';
  update public.packages set name = 'by collector' where external_key = 'AUTO-1';
end
$$;
reset role;
do $$
declare n bigint;
begin
  select count(*) into n from public.audit_log where table_name = 'packages' and after ->> 'external_key' = 'AUTO-1';
  assert n = 0, 'collector changes are not logged as manual edits';
end
$$;

rollback;
\echo 'OK  40_manual_entry'
