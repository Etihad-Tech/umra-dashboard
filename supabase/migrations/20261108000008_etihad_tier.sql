-- Phase 3 / migration 8: ETIHAD's own API publishes tier as comfort | lux | premium.
-- 'LUX' and 'PREMIUM' are already mapped; 'COMFORT' (English spelling) was not.
insert into public.category_map (raw, tier, note) values
  ('COMFORT', 'comfort', 'ETIHAD partner API')
on conflict (raw) do nothing;
