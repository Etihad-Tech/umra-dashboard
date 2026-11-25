-- Migration: tier (premium/lux/comfort) classification for Safar Travel package names.
--
-- Without this, EVERY Safar Travel package shows "mos paket yo'q" (no match) against our own
-- packages, because the comparison in index.html (genC) only ever compares packages whose tier
-- is mapped through category_map — Safar Travel's TypePackage values (MILLIY-14, ADDRESS-9, ...)
-- are plain package/hotel-brand names, not premium/lux/comfort labels, so category_map had 0
-- entries for them and every one of the ~60 names resolved to tier = NULL.
--
-- This is a BEST-EFFORT, PRICE-BASED first pass (average PriceQUAD across the sample data this
-- session pulled from the live API): >= $1700 -> premium, $1200-1699 -> lux, < $1200 -> comfort.
-- It is a starting point, not a verdict — edit rows directly in the category_map table (by `raw`,
-- which must stay UPPERCASE to match) whenever the business knows a package belongs elsewhere.

insert into public.category_map (raw, tier, note) values
  ('ADDRESS-10', 'premium', 'Safar Travel — price-based guess, refine by hand'),
  ('ADDRESS-14', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('ADDRESS-7', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('ADDRESS-9', 'premium', 'Safar Travel — price-based guess, refine by hand'),
  ('EBAA-14', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('EBAA-7', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('EBAA-9', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('FURSAT-14', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('IMKON-14', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('IMKON-7', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('IMKON-9', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('JUMEIRAH-7', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('MANZUR-14', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLENNIUM 10', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLENNIUM 10 PRO', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLENNIUM-11', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLENNIUM-13', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLENNIUM-14', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLENNIUM-14 PRO', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLENNIUM-14 PRO MAX', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLENNIUM-7', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLIY-12', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLIY-14', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLIY-14 PRO', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLIY-7', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('MILLIY-9 PRO', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('MUSTAQILLIK - 7', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R1: VIOLET 10', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R1: VIOLET 11', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R1: VIOLET 14', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R1: VIOLET 7', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R1: VIOLET-10 ECO', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R1: VIOLET-10 PRO', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R1: VIOLET-11 ECO', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R1: VIOLET-11 PRO', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R1: YAQEEN-10', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('R2: ALFAJR-13', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('R2: VIOLET-10', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('R2: VIOLET-14', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('R2: VIOLET-7', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('R2: YAQEEN-10', 'premium', 'Safar Travel — price-based guess, refine by hand'),
  ('R3: ALFAJR-14', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('R3: MILLENNIUM 10 PRO', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('R3: MILLENNIUM-10', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('R3: MILLENNIUM-10 PRO', 'premium', 'Safar Travel — price-based guess, refine by hand'),
  ('R3: MILLENNIUM-11', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('R3: MILLENNIUM-14', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('R3: MILLENNIUM-7', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('ROTANA 10', 'premium', 'Safar Travel — price-based guess, refine by hand'),
  ('ROTANA 7', 'premium', 'Safar Travel — price-based guess, refine by hand'),
  ('ROTANA 7 PRO', 'premium', 'Safar Travel — price-based guess, refine by hand'),
  ('SAFAR-14 PRO', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('SAFAR-8', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('SWISS-10', 'premium', 'Safar Travel — price-based guess, refine by hand'),
  ('SWISS-7', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('TAJ 11', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('TAJ-11 PRO', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('TAJ-14', 'lux', 'Safar Travel — price-based guess, refine by hand'),
  ('VIOLET-7', 'comfort', 'Safar Travel — price-based guess, refine by hand'),
  ('YAQEEN-10', 'lux', 'Safar Travel — price-based guess, refine by hand')
on conflict (raw) do update set tier = excluded.tier, note = excluded.note;
