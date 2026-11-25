-- Migration: fifth competitor, Safar Travel — read through SafarAgent, our own legitimate agent
-- login to Safar Travel's dashboard (dashboard.safartravel.uz), via its backing JSON API.
-- Constraint names are the PostgreSQL defaults for the inline checks in migration 1.

alter table public.companies drop constraint if exists companies_id_check;
alter table public.companies add  constraint companies_id_check check (id in ('B', 'S', 'F', 'H', 'T'));

alter table public.sources drop constraint if exists sources_kind_check;
alter table public.sources add  constraint sources_kind_check
  check (kind in (
    'mandarin_json',
    'safo_supabase',
    'etihad_showroom',
    'hikmat_html',
    'umra_uz_etihad',
    'umra_uz_safo',
    'safar_travel_api',
    'manual'
  ));

insert into public.companies (id, name, short) values ('T', 'Safar Travel', 'Safar Travel')
on conflict (id) do nothing;

insert into public.sources (company_id, kind, interval_min, config) values
  ('T', 'safar_travel_api', 30, '{"departure_city": "TOSHKENT"}')
on conflict (company_id, kind) do nothing;

-- No category_map entries: TypePackage (e.g. MILLIY-14, ADDRESS-9) is a package name, not a tier
-- label, same situation as Hikmat's MAXSUS/BARAKA/AFZAL — tier stays NULL until classified by hand.
