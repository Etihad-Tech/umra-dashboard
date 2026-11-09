-- Phase 5 follow-up / migration 7: replace the profile_names VIEW with a plain table.
--
-- Why: the view from migration 6 was SECURITY DEFINER and read auth.users (to fall back to the email prefix).
-- Supabase's security advisor flags that as CRITICAL ("Exposed Auth Users" / "Security Definer View"), and the email
-- prefix of colleagues was visible to every active user. The table below never touches auth.users: it only holds a
-- display name taken from profiles.full_name (set by an admin), or a neutral "user-xxxx" label when it is empty.
--
-- Access: active users can read it (to show "who entered this price"); nobody can write it directly. It is kept in
-- sync by a trigger on profiles. Deactivated people are removed from it.

drop view if exists public.profile_names;

create table public.profile_names (
  user_id      uuid primary key references auth.users (id) on delete cascade,
  display_name text not null
);

alter table public.profile_names enable row level security;

create policy profile_names_select on public.profile_names
  for select to authenticated using (app.is_active_user());

revoke all on public.profile_names from anon, authenticated;
grant select on public.profile_names to authenticated;
grant all    on public.profile_names to service_role;

create function app.sync_profile_name() returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    delete from public.profile_names where user_id = old.user_id;
    return old;
  end if;
  if new.active then
    insert into public.profile_names (user_id, display_name)
    values (new.user_id, coalesce(nullif(btrim(new.full_name), ''), 'user-' || left(new.user_id::text, 4)))
    on conflict (user_id) do update set display_name = excluded.display_name;
  else
    delete from public.profile_names where user_id = new.user_id;
  end if;
  return new;
end
$$;

revoke all on function app.sync_profile_name() from public;

create trigger sync_profile_name
  after insert or update of full_name, active or delete on public.profiles
  for each row execute function app.sync_profile_name();

-- existing people
insert into public.profile_names (user_id, display_name)
select p.user_id, coalesce(nullif(btrim(p.full_name), ''), 'user-' || left(p.user_id::text, 4))
  from public.profiles p
 where p.active
on conflict (user_id) do nothing;
