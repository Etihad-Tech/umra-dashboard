-- Phase 1 / migration 4: reference data (no business data, no secrets).

insert into public.companies (id, name, short) values
  ('B', 'ETIHAD',          'ETIHAD'),
  ('S', 'Safo Marva Tour', 'Safo'),
  ('F', 'Family Mandarin', 'Family M.');

-- One row per source. Intervals are the default 15 min (open question Q5 in the task).
-- Mandarin: departure city filter is configurable; default TOSHKENT (open question Q6).
insert into public.sources (company_id, kind, interval_min, config) values
  ('F', 'mandarin_json',   15, '{"departure_city": "TOSHKENT"}'),
  ('S', 'safo_supabase',   15, '{}'),
  ('B', 'etihad_showroom', 15, '{}');

-- Category normalisation. Departure city (TOSHKENT / NAMANGAN / URGANCH) is NOT a category and is not listed.
-- Matching rule (our "comfort" vs competitor KOMFORT/EKONOM/STANDART) is applied later, in the dashboard.
insert into public.category_map (raw, tier, note) values
  ('PREMIUM',       'premium', null),
  ('LUX',           'lux',     null),
  ('LUXE',          'lux',     'Mandarin spelling'),
  ('KOMFORT',       'comfort', null),
  ('KOMFORT PLUS',  'comfort', 'Mandarin'),
  ('EKONOM',        'econom',  null),
  ('STANDART',      'econom',  null),
  ('STANDART PLUS', 'econom',  'Mandarin');
