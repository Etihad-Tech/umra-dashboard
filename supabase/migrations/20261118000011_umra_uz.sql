-- Migration: umra.uz sources for Etihad and Safar Travel
-- Adds two new source kinds and their category_map entries.
-- Run once; idempotent via ON CONFLICT DO NOTHING.

-- 1. Expand sources.kind constraint to include the two new kinds
alter table public.sources drop constraint if exists sources_kind_check;
alter table public.sources add constraint sources_kind_check
  check (kind in (
    'mandarin_json',
    'safo_supabase',
    'etihad_showroom',
    'hikmat_html',
    'umra_uz_etihad',
    'umra_uz_safo',
    'manual'
  ));

-- 2. Register sources (interval_min=30 for politeness: 2 pages × 15 min)
insert into public.sources (company_id, kind, interval_min, config) values
  ('B', 'umra_uz_etihad', 30, '{}'),
  ('S', 'umra_uz_safo',   30, '{}')
on conflict (company_id, kind) do nothing;

-- 3. Category map for umra.uz classLabel values
--    Raw values on the site are title-cased (Lyuks, Premium, Komfort, Standart)
insert into public.category_map (raw, tier, note) values
  ('Lyuks',    'lux',     'umra.uz classLabel'),
  ('Premium',  'premium', 'umra.uz classLabel'),
  ('Komfort',  'comfort', 'umra.uz classLabel'),
  ('Standart', 'comfort', 'umra.uz classLabel')
on conflict (raw) do nothing;
