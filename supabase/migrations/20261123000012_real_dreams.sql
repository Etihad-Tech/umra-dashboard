-- Phase 9 / migration 12: fifth competitor, Real Dreams (umra.uz, same Next.js format as Etihad/Safar Travel).
-- Constraint names are the PostgreSQL defaults for the inline checks in migration 1.
alter table public.companies drop constraint if exists companies_id_check;
alter table public.companies add  constraint companies_id_check check (id in ('B', 'S', 'F', 'H', 'R'));

alter table public.sources drop constraint if exists sources_kind_check;
alter table public.sources add  constraint sources_kind_check
  check (kind in (
    'mandarin_json',
    'safo_supabase',
    'etihad_showroom',
    'hikmat_html',
    'umra_uz_etihad',
    'umra_uz_safo',
    'umra_uz_real_dreams',
    'manual'
  ));

insert into public.companies (id, name, short) values ('R', 'Real Dreams', 'Real Dreams')
on conflict (id) do nothing;

-- interval_min=30, same politeness window as the other two umra.uz sources (3 pages share one site now)
insert into public.sources (company_id, kind, interval_min, config) values ('R', 'umra_uz_real_dreams', 30, '{}')
on conflict (company_id, kind) do nothing;

-- No new category_map rows needed: Real Dreams publishes the same umra.uz classLabel values
-- (Lyuks / Premium / Komfort / Standart) already mapped by migration 20261118000011_umra_uz.sql.
