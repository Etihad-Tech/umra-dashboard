-- An admin without a 2FA (aal2) session has viewer rights only.

begin;
\ir 00_fixture.sql

-- ---------------------------------------- admin, password only (aal1)
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated","aal":"aal1"}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  assert app.user_role() = 'viewer', format('aal1 admin must act as viewer, got %s', app.user_role());
  select count(*) into n from public.app_settings;  assert n = 0, 'aal1 admin must not read app_settings';
  select count(*) into n from public.audit_log;     assert n = 0, 'aal1 admin must not read audit_log';
  select count(*) into n from public.profiles;      assert n = 1, 'aal1 admin must see only their own profile';
  select count(*) into n from public.departures;    assert n > 0,  'aal1 admin still reads business data as a viewer';

  update public.profiles set role = 'viewer' where user_id = '00000000-0000-0000-0000-0000000000e1';
  get diagnostics n = row_count;
  assert n = 0, 'aal1 admin must not change other profiles';

  begin
    insert into public.sources (company_id, kind) values ('B', 'manual');
    assert false, 'aal1 admin must not manage sources';
  exception when insufficient_privilege or check_violation then null;
  end;
end
$$;
reset role;

-- a token with no aal claim at all counts as aal1
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$ begin assert app.user_role() = 'viewer', 'admin without an aal claim must act as viewer'; end $$;
reset role;

-- ---------------------------------------- admin with 2FA (aal2)
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated","aal":"aal2"}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  assert app.user_role() = 'admin', 'aal2 admin must be admin';
  select count(*) into n from public.app_settings;  assert n = 1, 'aal2 admin reads app_settings';
  select count(*) into n from public.profiles;      assert n = 4, 'aal2 admin sees all profiles';
end
$$;
reset role;

-- ---------------------------------------- editors and viewers are not affected by aal
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000e1","role":"authenticated","aal":"aal1"}', true) \gset
set local role authenticated;
do $$ begin assert app.user_role() = 'editor', 'editor keeps editor rights at aal1'; end $$;
reset role;

-- ---------------------------------------- a deactivated admin gets nothing, even with 2FA
update public.profiles set active = false where user_id = '00000000-0000-0000-0000-0000000000a1';
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated","aal":"aal2"}', true) \gset
set local role authenticated;
do $$ begin assert app.user_role() is null, 'deactivated admin must have no role'; end $$;
reset role;

rollback;
\echo 'OK  30_admin_requires_2fa'
