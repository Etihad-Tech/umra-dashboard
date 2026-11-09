-- Review fixes / migration 11: the same RLS rules, evaluated once per query instead of once per row.
--
-- A bare call such as app.is_active_user() in a policy runs for EVERY row (it is a SECURITY DEFINER function, so
-- Postgres cannot inline it). On 20,000 price_snapshots rows that was ~90x slower than without RLS.
-- Wrapped as (select app.is_active_user()) Postgres runs it once per query (an "InitPlan").
-- ALTER POLICY changes only the expressions: command, roles and names stay exactly as in migrations 3, 6 and 7.

-- ----------------------------------------------------- read: any active user
do $$
declare
  t text;
begin
  foreach t in array array[
    'companies', 'sources', 'packages', 'departures', 'collector_runs',
    'price_snapshots', 'manual_entries', 'package_matches', 'events', 'category_map'
  ] loop
    execute format('alter policy %I on public.%I using ((select app.is_active_user()))', t || '_select_active', t);
  end loop;
end
$$;

alter policy profile_names_select on public.profile_names
  using ((select app.is_active_user()));

-- ----------------------------------------------------- editor / admin writes
alter policy manual_entries_insert on public.manual_entries
  with check ((select app.has_role(array['editor', 'admin'])) and created_by = (select auth.uid()));

alter policy manual_entries_update on public.manual_entries
  using ((select app.has_role(array['editor', 'admin'])))
  with check ((select app.has_role(array['editor', 'admin'])));

alter policy package_matches_insert on public.package_matches
  with check ((select app.has_role(array['editor', 'admin'])) and created_by = (select auth.uid()));

alter policy package_matches_update on public.package_matches
  using ((select app.has_role(array['editor', 'admin'])))
  with check ((select app.has_role(array['editor', 'admin'])));

alter policy packages_manual_insert on public.packages
  with check ((select app.has_role(array['editor', 'admin'])) and is_manual);

alter policy packages_manual_update on public.packages
  using ((select app.has_role(array['editor', 'admin'])) and is_manual)
  with check ((select app.has_role(array['editor', 'admin'])) and is_manual);

alter policy departures_manual_insert on public.departures
  with check ((select app.has_role(array['editor', 'admin'])) and is_manual);

alter policy departures_manual_update on public.departures
  using ((select app.has_role(array['editor', 'admin'])) and is_manual)
  with check ((select app.has_role(array['editor', 'admin'])) and is_manual);

-- --------------------------------------------------------------- admin only
alter policy sources_admin_write on public.sources
  using ((select app.has_role(array['admin']))) with check ((select app.has_role(array['admin'])));

alter policy category_map_admin_write on public.category_map
  using ((select app.has_role(array['admin']))) with check ((select app.has_role(array['admin'])));

alter policy app_settings_admin_all on public.app_settings
  using ((select app.has_role(array['admin']))) with check ((select app.has_role(array['admin'])));

alter policy profiles_admin_all on public.profiles
  using ((select app.has_role(array['admin']))) with check ((select app.has_role(array['admin'])));

alter policy profiles_select_self on public.profiles
  using (user_id = (select auth.uid()));

alter policy audit_log_select_admin on public.audit_log
  using ((select app.has_role(array['admin'])));
