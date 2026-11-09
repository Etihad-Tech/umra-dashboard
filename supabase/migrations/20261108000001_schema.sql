-- Phase 1 / migration 1: core schema.
-- Conventions: money = integer USD, NULL means "no data" (never 0).
--              all timestamps are timestamptz (the UI renders them in Asia/Tashkent).

create schema if not exists app; -- helper functions; NOT exposed through the API

-- ---------------------------------------------------------------- companies
create table public.companies (
  id    text primary key check (id in ('B', 'S', 'F')), -- B = ETIHAD, S = SAFO, F = MANDARIN
  name  text not null,
  short text not null,
  color text
);

-- ------------------------------------------------------------------ sources
create table public.sources (
  id                   bigint generated always as identity primary key,
  company_id           text not null references public.companies (id),
  kind                 text not null check (kind in ('mandarin_json', 'safo_supabase', 'etihad_showroom', 'manual')),
  enabled              boolean not null default true,
  interval_min         integer not null default 15 check (interval_min between 5 and 1440),
  config               jsonb not null default '{}'::jsonb, -- non-secret settings only (e.g. departure_city filter)
  last_ok_at           timestamptz,
  last_error           text,
  consecutive_failures integer not null default 0 check (consecutive_failures >= 0),
  down_since           timestamptz,
  unique (company_id, kind)
);

-- ----------------------------------------------------------------- packages
create table public.packages (
  id             bigint generated always as identity primary key,
  company_id     text not null references public.companies (id),
  external_key   text not null,                 -- e.g. FAJR-14, swiss10
  name           text,
  category_raw   text,                          -- exactly as the source publishes it
  tier           text check (tier in ('premium', 'lux', 'comfort', 'econom')), -- NULL = unmapped
  nights         integer check (nights > 0),
  departure_city text,                          -- Mandarin "company" field; NOT a category
  hotels         jsonb not null default '[]'::jsonb,
  services       jsonb not null default '{}'::jsonb,
  active         boolean not null default true,
  first_seen_at  timestamptz not null default now()
);
create unique index packages_company_key_city_uq
  on public.packages (company_id, external_key, coalesce(departure_city, ''));
create index packages_tier_idx on public.packages (tier) where active;

-- --------------------------------------------------------------- departures
create table public.departures (
  id            bigint generated always as identity primary key,
  package_id    bigint not null references public.packages (id) on delete cascade,
  date          date not null,
  return_date   date,
  sold_out      boolean not null default false,
  seats_left    integer check (seats_left >= 0), -- NULL = source does not publish it
  active        boolean not null default true,
  missing_runs  integer not null default 0 check (missing_runs >= 0), -- "removed" event after 2 runs in a row
  first_seen_at timestamptz not null default now(),
  unique (package_id, date),
  check (return_date is null or return_date >= date)
);
create index departures_date_idx on public.departures (date) where active;

-- one row per run per source
create table public.collector_runs (
  id          bigint generated always as identity primary key,
  source_id   bigint not null references public.sources (id) on delete cascade,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  status      text not null default 'running' check (status in ('running', 'ok', 'failed')),
  items_seen  integer check (items_seen >= 0),
  changes     integer check (changes >= 0),
  error       text
);
create index collector_runs_source_idx on public.collector_runs (source_id, started_at desc);

-- ----------------------------------------------------------- price_snapshots
-- Append-only history. A row is written only when the value differs from the last one.
create table public.price_snapshots (
  id           bigint generated always as identity primary key,
  departure_id bigint not null references public.departures (id) on delete cascade,
  pax          smallint not null check (pax between 1 and 4),
  price_usd    integer check (price_usd > 0),  -- NULL = no price published
  seen_at      timestamptz not null default now(),
  source_id    bigint references public.sources (id) on delete set null,
  run_id       bigint references public.collector_runs (id) on delete set null
);
create index price_snapshots_lookup_idx on public.price_snapshots (departure_id, pax, seen_at desc, id desc);

-- ------------------------------------------------------------ manual_entries
create table public.manual_entries (
  id           bigint generated always as identity primary key,
  departure_id bigint not null references public.departures (id) on delete cascade,
  pax          smallint check (pax between 1 and 4),
  price_usd    integer check (price_usd > 0),
  sold_out     boolean,
  note         text,
  created_by   uuid not null default auth.uid() references auth.users (id),
  created_at   timestamptz not null default now(),
  valid_until  timestamptz not null default (now() + interval '7 days'),
  cleared_at   timestamptz,
  check (price_usd is null or pax is not null),
  check (price_usd is not null or sold_out is not null),
  check (valid_until > created_at)
);
create index manual_entries_lookup_idx on public.manual_entries (departure_id, pax, created_at desc);

-- ----------------------------------------------------------- package_matches
create table public.package_matches (
  id               bigint generated always as identity primary key,
  our_package_id   bigint not null references public.packages (id) on delete cascade,
  their_package_id bigint not null references public.packages (id) on delete cascade,
  created_by       uuid not null default auth.uid() references auth.users (id),
  created_at       timestamptz not null default now(),
  note             text,
  active           boolean not null default true, -- "unmatch" = set false (no deletes for editors)
  unique (our_package_id, their_package_id),
  check (our_package_id <> their_package_id)
);

-- ------------------------------------------------------------------- events
create table public.events (
  id               bigint generated always as identity primary key,
  type             text not null check (type in (
                     'price_down', 'price_up', 'new_departure', 'new_package',
                     'sold_out', 'back_on_sale', 'removed',
                     'source_down', 'source_recovered',
                     'manual_entry', 'manual_conflict')),
  company_id       text references public.companies (id),
  package_id       bigint references public.packages (id) on delete set null,
  departure_id     bigint references public.departures (id) on delete set null,
  old              jsonb,
  new              jsonb,
  is_manual        boolean not null default false,
  detected_at      timestamptz not null default now(),
  telegram_sent_at timestamptz
);
create index events_unsent_idx on public.events (detected_at) where telegram_sent_at is null;
create index events_feed_idx on public.events (detected_at desc);

-- ----------------------------------------------------------------- profiles
create table public.profiles (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  full_name  text not null default '',
  role       text not null default 'viewer' check (role in ('admin', 'editor', 'viewer')),
  active     boolean not null default true,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- audit_log
create table public.audit_log (
  id         bigint generated always as identity primary key,
  actor      uuid,
  action     text not null,
  table_name text not null,
  row_id     text,
  before     jsonb,
  after      jsonb,
  at         timestamptz not null default now()
);
create index audit_log_at_idx on public.audit_log (at desc);

-- ------------------------------------------------------------- configuration
-- Raw source category -> normalized tier. Kept in a table, not hard-coded.
create table public.category_map (
  raw  text primary key check (raw = upper(btrim(raw))),
  tier text not null check (tier in ('premium', 'lux', 'comfort', 'econom')),
  note text
);

-- Non-secret app settings (Telegram chat id, allowed recipients, ...). The bot token is NEVER stored here.
create table public.app_settings (
  key        text primary key,
  value      jsonb not null,
  updated_by uuid references auth.users (id),
  updated_at timestamptz not null default now()
);

-- -------------------------------------------------------------------- views
-- Latest price per departure x pax, manual override applied.
-- conflict = the automatic source changed to a DIFFERENT value after the manual entry was made.
create view public.price_current with (security_invoker = true) as
with latest_auto as (
  select distinct on (departure_id, pax)
         departure_id, pax, price_usd, seen_at
    from public.price_snapshots
   order by departure_id, pax, seen_at desc, id desc
), latest_manual as (
  select distinct on (departure_id, pax)
         id, departure_id, pax, price_usd, created_by, created_at, valid_until
    from public.manual_entries
   where pax is not null
     and price_usd is not null
     and cleared_at is null
     and valid_until > now()
   order by departure_id, pax, created_at desc, id desc
)
select coalesce(m.departure_id, a.departure_id)  as departure_id,
       coalesce(m.pax, a.pax)                    as pax,
       coalesce(m.price_usd, a.price_usd)        as price_usd,
       (m.id is not null)                        as is_manual,
       a.price_usd                               as auto_price_usd,
       a.seen_at                                 as auto_seen_at,
       (m.id is not null
          and a.seen_at > m.created_at
          and a.price_usd is distinct from m.price_usd) as conflict,
       m.id                                      as manual_entry_id,
       m.created_by                              as manual_by,
       m.created_at                              as manual_at,
       m.valid_until                             as manual_valid_until
  from latest_auto a
  full join latest_manual m
    on m.departure_id = a.departure_id and m.pax = a.pax;

-- Categories the mapper did not recognise: shown as "unmapped", never silently dropped.
create view public.unmapped_categories with (security_invoker = true) as
select company_id, category_raw, count(*) as packages
  from public.packages
 where tier is null and category_raw is not null and btrim(category_raw) <> ''
 group by company_id, category_raw;
