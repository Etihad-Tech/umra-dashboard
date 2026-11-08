-- Acceptance test (task section 6.2): a logged-out request with the anon key returns 0 rows from EVERY table.
-- Three independent checks, so a mistake in one layer is caught by another.

begin;
\ir 00_fixture.sql

-- 1. RLS is ON for every public table and every table has at least one policy.
do $$
declare
  r record;
begin
  for r in
    select c.relname,
           c.relrowsecurity,
           (select count(*) from pg_policies p where p.schemaname = 'public' and p.tablename = c.relname) as npol
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind in ('r', 'p')
  loop
    assert r.relrowsecurity, format('RLS is OFF on public.%s', r.relname);
    assert r.npol > 0,       format('public.%s has no policy', r.relname);
  end loop;
end
$$;

-- 2. anon holds no privilege at all on any public table or view.
do $$
declare
  r record;
  p text;
begin
  for r in
    select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind in ('r', 'p', 'v')
  loop
    foreach p in array array['select', 'insert', 'update', 'delete'] loop
      assert not has_table_privilege('anon', format('public.%I', r.relname), p),
             format('anon has %s on public.%s', upper(p), r.relname);
    end loop;
  end loop;
end
$$;

-- 3. Worst case: even if someone grants anon SELECT everywhere, RLS still returns 0 rows.
grant select on all tables in schema public to anon;   -- undone by the rollback below

do $$
declare
  r record;
  n bigint;
  seen int := 0;
begin
  for r in
    select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind in ('r', 'p', 'v')
  loop
    set local role anon;
    execute format('select count(*) from public.%I', r.relname) into n;
    reset role;
    assert n = 0, format('LEAK: anon sees %s rows in public.%s', n, r.relname);
    seen := seen + 1;
  end loop;
  assert seen >= 15, format('expected to check all tables and views, only checked %s', seen);
end
$$;

rollback;
\echo 'OK  10_rls_anon'
