-- Provision a public.profiles row for every new auth user.
--
-- BUG (found 2026-10-01 during a real end-to-end signup): a user who signs up
-- through Supabase Auth never gets a public.profiles row. Nearly every domain
-- table references profiles(id) (firm_memberships, households,
-- interview_sessions, audit_events, ...), so the first profile-dependent call
-- fails -- e.g. POST /v1/invitations/accept for a firm role returned HTTP 500
-- (FK violation on firm_memberships.user_id) for a freshly signed-up user.
--
-- The trigger below is the standard Supabase pattern: after insert on
-- auth.users, insert the matching profiles row (idempotent via
-- ON CONFLICT DO NOTHING so replays are safe). The backfill covers auth users
-- created before this migration (only rows with no matching profile).

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, display_name)
  values (
    new.id,
    new.email,
    coalesce(
      nullif(new.raw_user_meta_data ->> 'display_name', ''),
      nullif(new.raw_user_meta_data ->> 'full_name', ''),
      split_part(coalesce(new.email, ''), '@', 1)
    )
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;

create trigger on_auth_user_created
  after insert on auth.users
  for each row
  execute procedure public.handle_new_user();

-- Backfill: auth users created before this migration that have no profile.
insert into public.profiles (id, email, display_name)
select
  u.id,
  u.email,
  split_part(coalesce(u.email, ''), '@', 1)
from auth.users u
left join public.profiles p on p.id = u.id
where p.id is null
on conflict (id) do nothing;
