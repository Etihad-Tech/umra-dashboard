-- Phase 1 / migration 2: helper functions and triggers.

-- ------------------------------------------------------------- role helpers
-- SECURITY DEFINER so policies can read profiles without recursing into profiles' own RLS.
create function app.user_role() returns text
language sql stable security definer set search_path = ''
as $$
  select p.role from public.profiles p where p.user_id = auth.uid() and p.active
$$;

create function app.is_active_user() returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (select 1 from public.profiles p where p.user_id = auth.uid() and p.active)
$$;

create function app.has_role(roles text[]) returns boolean
language sql stable security definer set search_path = ''
as $$
  select coalesce(app.user_role() = any (roles), false)
$$;

-- ---------------------------------------------------------------- audit_log
-- Append-only: nobody (including service role) can update/delete/truncate it.
create function app.audit_log_immutable() returns trigger
language plpgsql set search_path = ''
as $$
begin
  raise exception 'audit_log is append-only';
end
$$;

create trigger audit_log_no_row_change
  before update or delete on public.audit_log
  for each row execute function app.audit_log_immutable();

create trigger audit_log_no_truncate
  before truncate on public.audit_log
  for each statement execute function app.audit_log_immutable();

-- Logs changes made by a logged-in person (auth.uid() is set).
-- Collector / service-role / SQL-console changes have no uid and are not "by hand" edits.
create function app.audit_row() returns trigger
language plpgsql security definer set search_path = ''
as $$
declare
  v_old jsonb := case when tg_op in ('UPDATE', 'DELETE') then to_jsonb(old) end;
  v_new jsonb := case when tg_op in ('INSERT', 'UPDATE') then to_jsonb(new) end;
begin
  if auth.uid() is not null then
    insert into public.audit_log (actor, action, table_name, row_id, before, after)
    values (
      auth.uid(), tg_op, tg_table_name,
      coalesce(v_new ->> 'id', v_old ->> 'id', v_new ->> 'user_id', v_old ->> 'user_id', v_new ->> 'key', v_old ->> 'key'),
      v_old, v_new
    );
  end if;
  return coalesce(new, old);
end
$$;

create trigger audit_manual_entries  after insert or update or delete on public.manual_entries  for each row execute function app.audit_row();
create trigger audit_package_matches after insert or update or delete on public.package_matches for each row execute function app.audit_row();
create trigger audit_profiles        after insert or update or delete on public.profiles        for each row execute function app.audit_row();
create trigger audit_sources         after insert or update or delete on public.sources         for each row execute function app.audit_row();
create trigger audit_category_map    after insert or update or delete on public.category_map    for each row execute function app.audit_row();
create trigger audit_app_settings    after insert or update or delete on public.app_settings    for each row execute function app.audit_row();

-- ------------------------------------------------------------ manual_entries
-- After creation only note / valid_until / cleared_at may change (price history stays honest).
create function app.manual_entries_guard() returns trigger
language plpgsql set search_path = ''
as $$
begin
  if (new.id, new.departure_id, new.pax, new.price_usd, new.sold_out, new.created_by, new.created_at)
     is distinct from
     (old.id, old.departure_id, old.pax, old.price_usd, old.sold_out, old.created_by, old.created_at) then
    raise exception 'manual_entries: only note, valid_until and cleared_at can be changed';
  end if;
  return new;
end
$$;

create trigger manual_entries_guard
  before update on public.manual_entries
  for each row execute function app.manual_entries_guard();

-- Every manual entry produces an event (the notifier sends it to Telegram, marked as manual).
create function app.manual_entry_event() returns trigger
language plpgsql security definer set search_path = ''
as $$
declare
  v_package bigint;
  v_company text;
  v_auto    integer;
begin
  select d.package_id, p.company_id into v_package, v_company
    from public.departures d join public.packages p on p.id = d.package_id
   where d.id = new.departure_id;

  select ps.price_usd into v_auto
    from public.price_snapshots ps
   where ps.departure_id = new.departure_id and ps.pax = new.pax
   order by ps.seen_at desc, ps.id desc
   limit 1;

  insert into public.events (type, company_id, package_id, departure_id, old, new, is_manual)
  values (
    'manual_entry', v_company, v_package, new.departure_id,
    jsonb_build_object('pax', new.pax, 'price_usd', v_auto),
    jsonb_build_object('pax', new.pax, 'price_usd', new.price_usd, 'sold_out', new.sold_out, 'note', new.note),
    true
  );
  return new;
end
$$;

create trigger manual_entry_event
  after insert on public.manual_entries
  for each row execute function app.manual_entry_event();

-- ----------------------------------------------------------- package_matches
-- "our" side must be ETIHAD (B); "their" side must be a competitor.
create function app.package_matches_check() returns trigger
language plpgsql set search_path = ''
as $$
declare
  v_our   text;
  v_their text;
begin
  select company_id into v_our   from public.packages where id = new.our_package_id;
  select company_id into v_their from public.packages where id = new.their_package_id;
  if v_our is distinct from 'B' then
    raise exception 'package_matches: our_package_id must belong to company B (ETIHAD)';
  end if;
  if v_their is null or v_their = 'B' then
    raise exception 'package_matches: their_package_id must belong to a competitor';
  end if;
  return new;
end
$$;

create trigger package_matches_check
  before insert or update of our_package_id, their_package_id on public.package_matches
  for each row execute function app.package_matches_check();

-- ------------------------------------------------------------- new app users
-- Sign-up is disabled, so a new auth.users row always comes from an admin invite.
-- It starts as an active viewer; an admin raises the role afterwards.
create function app.handle_new_user() returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  insert into public.profiles (user_id, full_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'full_name', ''))
  on conflict (user_id) do nothing;
  return new;
end
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function app.handle_new_user();
