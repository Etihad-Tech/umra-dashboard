-- Phase 6 / migration 9: Safo publishes its packages under slugs; map the ones whose tier is clear from the name.
-- The collector looks the slug up in UPPER case. Other Safo slugs stay unmapped (tier NULL) until the business decides.
insert into public.category_map (raw, tier, note) values
  ('ADRESS-LUX',                 'lux',     'Safo'),
  ('ADRESS-LUX-7-HAVO-YO-LLARI', 'lux',     'Safo'),
  ('XOJI-AKA-LUX',               'lux',     'Safo'),
  ('HYATT-PREMIUM',              'premium', 'Safo'),
  ('SWISS-MAQOM-PREMIUM',        'premium', 'Safo'),
  ('COMFORT-PLUS-11',            'comfort', 'Safo'),
  ('KOMFORT-PLUS',               'comfort', 'Safo')
on conflict (raw) do nothing;
