-- Phase 8 / migration 10: fourth competitor, Hikmat Umra (public HTML site, prices published as "from" per person).
-- Constraint names are the PostgreSQL defaults for the inline checks in migration 1.
alter table public.companies drop constraint if exists companies_id_check;
alter table public.companies add  constraint companies_id_check check (id in ('B', 'S', 'F', 'H'));

alter table public.sources drop constraint if exists sources_kind_check;
alter table public.sources add  constraint sources_kind_check
  check (kind in ('mandarin_json', 'safo_supabase', 'etihad_showroom', 'hikmat_html', 'manual'));

insert into public.companies (id, name, short) values ('H', 'Hikmat Umra Travel', 'Hikmat')
on conflict (id) do nothing;

insert into public.sources (company_id, kind, interval_min, config) values ('H', 'hikmat_html', 15, '{}')
on conflict (company_id, kind) do nothing;

-- Tier mapping. Only the clear cases are mapped; the rest stay unmapped (tier NULL) until the business decides.
--   STANDART already exists (econom).  ELITA = Address Jabal Omar 5* + Valy Madina 4*  ->  lux.
-- MAXSUS, BARAKA, AFZAL, ISTANBUL + UMRA are intentionally NOT mapped here.
insert into public.category_map (raw, tier, note) values
  ('ELITA', 'lux', 'Hikmat')
on conflict (raw) do nothing;
