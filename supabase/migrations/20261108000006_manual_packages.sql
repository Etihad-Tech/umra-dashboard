-- Phase 5 / migration 6: manual packages and dates, and names for the "who entered it" badge.

-- ------------------------------------------------- manual packages / dates
-- Rows added by hand carry is_manual = true. The collector never deactivates them when a source lacks them.
alter table public.packages   add column is_manual boolean not null default false;
alter table public.departures add column is_manual boolean not null default false;

-- Editors/admins may add a package or a date that a source is missing, and later fix or hide what THEY added.
-- Automatic rows stay untouchable: the policies only match is_manual rows, and is_manual cannot be switched off or on.
create policy packages_manual_insert on public.packages
  for insert to authenticated
  with check (app.has_role(array['editor', 'admin']) and is_manual);
create policy packages_manual_update on public.packages
  for update to authenticated
  using (app.has_role(array['editor', 'admin']) and is_manual)
  with check (app.has_role(array['editor', 'admin']) and is_manual);

create policy departures_manual_insert on public.departures
  for insert to authenticated
  with check (app.has_role(array['editor', 'admin']) and is_manual);
create policy departures_manual_update on public.departures
  for update to authenticated
  using (app.has_role(array['editor', 'admin']) and is_manual)
  with check (app.has_role(array['editor', 'admin']) and is_manual);

-- Column-level grants: RLS decides WHICH rows, these decide WHICH columns.
grant insert (company_id, external_key, name, category_raw, tier, nights, departure_city, is_manual)
  on public.packages to authenticated;
grant update (name, category_raw, tier, nights, active) on public.packages to authenticated;
grant insert (package_id, date, return_date, sold_out, is_manual) on public.departures to authenticated;
grant update (return_date, sold_out, active) on public.departures to authenticated;

-- Every manual change is logged (the trigger ignores the collector, which has no auth.uid()).
create trigger audit_packages   after insert or update or delete on public.packages   for each row execute function app.audit_row();
create trigger audit_departures after insert or update or delete on public.departures for each row execute function app.audit_row();

-- --------------------------------------------------------------- names
-- Editors can only read their own profile, but the badge must say WHO entered a price.
-- This view exposes only a display name, and only to active users.
create view public.profile_names with (security_invoker = false) as
select p.user_id,
       coalesce(nullif(btrim(p.full_name), ''), split_part(u.email, '@', 1)) as display_name
  from public.profiles p
  join auth.users u on u.id = p.user_id
 where p.active and app.is_active_user();

revoke all on public.profile_names from anon, authenticated;
grant select on public.profile_names to authenticated;
grant all on public.profile_names to service_role;
