-- Phase 1 / migration 3: Row Level Security and grants.
-- Rule: RLS on EVERY table, and every table has at least one policy.
--   viewer : SELECT on business tables
--   editor : viewer + INSERT/UPDATE on manual_entries and package_matches
--   admin  : editor + manage profiles, sources, category_map, app_settings; read audit_log
-- The collector uses the service role (bypasses RLS). The browser never does.
-- A deactivated user (profiles.active = false) gets nothing: every policy goes through app.is_active_user()/has_role().

alter table public.companies       enable row level security;
alter table public.sources         enable row level security;
alter table public.packages        enable row level security;
alter table public.departures      enable row level security;
alter table public.collector_runs  enable row level security;
alter table public.price_snapshots enable row level security;
alter table public.manual_entries  enable row level security;
alter table public.package_matches enable row level security;
alter table public.events          enable row level security;
alter table public.profiles        enable row level security;
alter table public.audit_log       enable row level security;
alter table public.category_map    enable row level security;
alter table public.app_settings    enable row level security;

-- ----------------------------------------------------- read: any active user
do $$
declare
  t text;
begin
  foreach t in array array[
    'companies', 'sources', 'packages', 'departures', 'collector_runs',
    'price_snapshots', 'manual_entries', 'package_matches', 'events', 'category_map'
  ] loop
    execute format(
      'create policy %I on public.%I for select to authenticated using (app.is_active_user())',
      t || '_select_active', t);
  end loop;
end
$$;

-- ----------------------------------------------------- editor / admin writes
create policy manual_entries_insert on public.manual_entries
  for insert to authenticated
  with check (app.has_role(array['editor', 'admin']) and created_by = auth.uid());

create policy manual_entries_update on public.manual_entries
  for update to authenticated
  using (app.has_role(array['editor', 'admin']))
  with check (app.has_role(array['editor', 'admin']));

create policy package_matches_insert on public.package_matches
  for insert to authenticated
  with check (app.has_role(array['editor', 'admin']) and created_by = auth.uid());

create policy package_matches_update on public.package_matches
  for update to authenticated
  using (app.has_role(array['editor', 'admin']))
  with check (app.has_role(array['editor', 'admin']));

-- --------------------------------------------------------------- admin only
create policy sources_admin_write on public.sources
  for all to authenticated
  using (app.has_role(array['admin'])) with check (app.has_role(array['admin']));

create policy category_map_admin_write on public.category_map
  for all to authenticated
  using (app.has_role(array['admin'])) with check (app.has_role(array['admin']));

create policy app_settings_admin_all on public.app_settings
  for all to authenticated
  using (app.has_role(array['admin'])) with check (app.has_role(array['admin']));

-- profiles: everyone reads their own row (to learn their role); admins read and manage all.
create policy profiles_select_self on public.profiles
  for select to authenticated using (user_id = auth.uid());

create policy profiles_admin_all on public.profiles
  for all to authenticated
  using (app.has_role(array['admin'])) with check (app.has_role(array['admin']));

-- audit_log: admins can read. No insert/update/delete policy: rows come only from triggers.
create policy audit_log_select_admin on public.audit_log
  for select to authenticated using (app.has_role(array['admin']));

-- ------------------------------------------------------------------- grants
-- Defence in depth: RLS above is the real gate, grants shrink what could ever be reached.
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;

grant select on all tables in schema public to authenticated;
grant insert, update         on public.manual_entries, public.package_matches to authenticated;
grant insert, update, delete on public.profiles, public.sources, public.category_map, public.app_settings to authenticated;

alter default privileges in schema public revoke all on tables    from anon;
alter default privileges in schema public revoke all on sequences from anon;

-- The collector / notifier connect as service_role: it bypasses RLS but still needs table privileges.
-- Granted explicitly so nothing depends on the project's "Automatically expose new tables" setting.
grant all on all tables    in schema public to service_role;
grant all on all sequences in schema public to service_role;
alter default privileges in schema public grant all on tables    to service_role;
alter default privileges in schema public grant all on sequences to service_role;

revoke all on schema app from public, anon;
grant usage on schema app to authenticated;
revoke execute on all functions in schema app from public, anon;
grant execute on function app.user_role(), app.is_active_user(), app.has_role(text[]) to authenticated;
