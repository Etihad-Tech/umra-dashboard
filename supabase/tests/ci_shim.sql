-- LOCAL / CI ONLY. Never run this on a real Supabase project (it already has all of this).
-- Gives a plain Postgres 15+ the few Supabase pieces the migrations rely on:
-- the anon / authenticated / service_role roles, auth.users, auth.mfa_factors, auth.uid() and auth.jwt().

do $$
begin
  if not exists (select from pg_roles where rolname = 'anon')          then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select from pg_roles where rolname = 'service_role')  then create role service_role nologin bypassrls; end if;
end
$$;

create schema if not exists auth;

create table if not exists auth.users (
  id                 uuid primary key default gen_random_uuid(),
  email              text,
  raw_user_meta_data jsonb not null default '{}'::jsonb
);
alter table auth.users add column if not exists is_anonymous boolean not null default false;

-- 2FA factors, as Supabase Auth keeps them (factor_type / status are enums there; text is enough here)
create table if not exists auth.mfa_factors (
  id            uuid primary key,
  user_id       uuid not null references auth.users (id) on delete cascade,
  friendly_name text,
  factor_type   text not null,
  status        text not null,
  created_at    timestamptz not null,
  updated_at    timestamptz not null
);

create or replace function auth.uid() returns uuid
language sql stable
as $$
  select nullif(
    coalesce(
      current_setting('request.jwt.claim.sub', true),
      nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
    ), '')::uuid
$$;

grant usage on schema auth, public to anon, authenticated, service_role;

-- Supabase's default: every NEW object in public is granted to the API roles (the migrations must undo this).
alter default privileges in schema public grant all on tables    to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;

-- Same behaviour as Supabase's auth.jwt(): the claims of the current request.
create or replace function auth.jwt() returns jsonb
language sql stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')
  )::jsonb
$$;
grant execute on function auth.jwt() to anon, authenticated, service_role;

