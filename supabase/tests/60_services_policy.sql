-- app_settings 'package_services' is readable by every active user; all other settings rows stay admin-only.

begin;
\ir 00_fixture.sql

insert into public.app_settings (key, value) values ('package_services', '{"B": {}, "S": {}, "F": {}}');

-- ================================================================= viewer: services row only
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000b1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  select count(*) into n from public.app_settings;                              assert n = 1, 'viewer must read exactly one settings row';
  select count(*) into n from public.app_settings where key = 'package_services'; assert n = 1, 'viewer must read package_services';
  select count(*) into n from public.app_settings where key = 'telegram';         assert n = 0, 'viewer must not read the telegram settings';

  update public.app_settings set value = '{}' where key = 'package_services';
  get diagnostics n = row_count;
  assert n = 0, 'viewer changed package_services';
exception when insufficient_privilege then null;   -- refused outright is fine too
end
$$;
reset role;

-- ================================================================= deactivated user: nothing
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000f1","role":"authenticated"}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  select count(*) into n from public.app_settings; assert n = 0, 'deactivated user read app_settings';
end
$$;
reset role;

-- ================================================================= anonymous sign-in: nothing
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-0000000000b1","role":"authenticated","is_anonymous":true}', true) \gset
set local role authenticated;
do $$
declare n bigint;
begin
  select count(*) into n from public.app_settings; assert n = 0, 'anonymous JWT read app_settings';
end
$$;
reset role;

-- ================================================================= anon key, logged out: nothing
select set_config('request.jwt.claims', '{"role":"anon"}', true) \gset
set local role anon;
do $$
declare n bigint;
begin
  select count(*) into n from public.app_settings; assert n = 0, 'anon read app_settings';
exception when insufficient_privilege then null;
end
$$;
reset role;

rollback;
\echo 'OK  60_services_policy'
