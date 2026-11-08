-- Test fixture. Included (\ir) inside a transaction by every test; the transaction is rolled back.
-- Runs as the migration owner (superuser / postgres), so RLS does not apply here.

insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-0000000000a1', 'admin@test'),
  ('00000000-0000-0000-0000-0000000000e1', 'editor@test'),
  ('00000000-0000-0000-0000-0000000000b1', 'viewer@test'),
  ('00000000-0000-0000-0000-0000000000f1', 'inactive@test');
-- the on_auth_user_created trigger made them active viewers; set the real roles:
update public.profiles set role = 'admin'  where user_id = '00000000-0000-0000-0000-0000000000a1';
update public.profiles set role = 'editor' where user_id = '00000000-0000-0000-0000-0000000000e1';
update public.profiles set role = 'editor', active = false where user_id = '00000000-0000-0000-0000-0000000000f1';

insert into public.packages (company_id, external_key, category_raw, tier, nights, departure_city) values
  ('B', 'swiss10',  'LUX',  'lux', 10, 'TOSHKENT'),
  ('F', 'HYATT-09', 'LUXE', 'lux',  9, 'TOSHKENT');

insert into public.departures (package_id, date)
select id, date '2026-12-12' from public.packages;

-- 4-person price and a missing 1-person price (must stay NULL, never 0)
insert into public.price_snapshots (departure_id, pax, price_usd, seen_at)
select id, 4, 2000, now() - interval '2 days' from public.departures;
insert into public.price_snapshots (departure_id, pax, price_usd, seen_at)
select id, 1, null, now() - interval '2 days' from public.departures;

insert into public.collector_runs (source_id, status) select id, 'ok' from public.sources limit 1;
insert into public.events (type, company_id) values ('price_down', 'F');
insert into public.app_settings (key, value) values ('telegram', '{"chat_id": "test"}');
insert into public.audit_log (actor, action, table_name, row_id)
values ('00000000-0000-0000-0000-0000000000a1', 'INSERT', 'fixture', '1');

insert into public.package_matches (our_package_id, their_package_id, created_by)
select o.id, t.id, '00000000-0000-0000-0000-0000000000a1'
  from public.packages o, public.packages t
 where o.company_id = 'B' and t.company_id = 'F';

insert into public.manual_entries (departure_id, pax, price_usd, created_by)
select d.id, 2, 2100, '00000000-0000-0000-0000-0000000000a1'
  from public.departures d join public.packages p on p.id = d.package_id
 where p.company_id = 'F';
