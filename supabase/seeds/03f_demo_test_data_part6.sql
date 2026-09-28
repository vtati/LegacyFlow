-- ============================================================================
-- 03f_demo_test_data_part6.sql — Part 6: household membership links + fixes
-- ============================================================================
-- Run AFTER part 5. ALL DATA IS SYNTHETIC. Idempotent: safe to re-run.
--
-- Links the seeded persons into household_members (primary_client /
-- collaborator / dependent / trusted_contact) so firm and family UIs that
-- read household membership see the household roster. Also applies two
-- corrections to data seeded by earlier parts:
--   1. Clears the account holder's date_of_birth (never supplied; must be NULL).
--   2. Backfills matter titles for any matter still untitled.
-- ============================================================================

do $$
declare
  v_email    constant text := 'tati.venugopal@gmail.com';
  v_user     uuid;

  v_hh_a     constant uuid := '20000000-0000-0000-0000-000000000002';
  v_hh_b     constant uuid := '20000000-0000-0000-0000-000000000003';

  v_matter_1 constant uuid := '20000000-0000-0000-0000-000000000004';
  v_matter_2 constant uuid := '20000000-0000-0000-0000-000000000005';

  v_p_user   constant uuid := '20000000-0000-0000-0000-000000000006';
  v_p_spouse constant uuid := '20000000-0000-0000-0000-000000000007';
  v_p_kid1   constant uuid := '20000000-0000-0000-0000-000000000008';
  v_p_kid2   constant uuid := '20000000-0000-0000-0000-000000000009';
  v_p_guard1 constant uuid := '20000000-0000-0000-0000-000000000010';
  v_p_guard2 constant uuid := '20000000-0000-0000-0000-000000000011';
  v_p_b1     constant uuid := '20000000-0000-0000-0000-000000000012';
begin
  select u.id into v_user from auth.users u where u.email = v_email;
  if v_user is null then
    raise exception 'auth user not found: % (sign in to the staging app once first)', v_email;
  end if;

  -- --- corrections to earlier parts (idempotent) ---
  update public.persons
     set date_of_birth = null
   where id = v_p_user and date_of_birth is not null;

  update public.matters
     set title = 'Tati Family Will'
   where id = v_matter_1 and (title is null or title = '');

  update public.matters
     set title = 'Blake Family Trust'
   where id = v_matter_2 and (title is null or title = '');

  -- --- household_members (household A) ---
  -- Part 1 already created (v_hh_a, v_user) without a person_id; link it.
  update public.household_members
     set person_id = v_p_user
   where household_id = v_hh_a and user_id = v_user and person_id is null;

  insert into public.household_members (household_id, user_id, person_id, relationship_role, access_status)
  select v_hh_a, null, v_p_spouse, 'collaborator', 'active'
  where not exists (select 1 from public.household_members
                    where household_id = v_hh_a and person_id = v_p_spouse);

  insert into public.household_members (household_id, user_id, person_id, relationship_role, access_status)
  select v_hh_a, null, v_p_kid1, 'dependent', 'active'
  where not exists (select 1 from public.household_members
                    where household_id = v_hh_a and person_id = v_p_kid1);

  insert into public.household_members (household_id, user_id, person_id, relationship_role, access_status)
  select v_hh_a, null, v_p_kid2, 'dependent', 'active'
  where not exists (select 1 from public.household_members
                    where household_id = v_hh_a and person_id = v_p_kid2);

  insert into public.household_members (household_id, user_id, person_id, relationship_role, access_status)
  select v_hh_a, null, v_p_guard1, 'trusted_contact', 'active'
  where not exists (select 1 from public.household_members
                    where household_id = v_hh_a and person_id = v_p_guard1);

  insert into public.household_members (household_id, user_id, person_id, relationship_role, access_status)
  select v_hh_a, null, v_p_guard2, 'trusted_contact', 'active'
  where not exists (select 1 from public.household_members
                    where household_id = v_hh_a and person_id = v_p_guard2);

  -- --- household_members (household B) ---
  insert into public.household_members (household_id, user_id, person_id, relationship_role, access_status)
  select v_hh_b, null, v_p_b1, 'primary_client', 'active'
  where not exists (select 1 from public.household_members
                    where household_id = v_hh_b and person_id = v_p_b1);

  raise notice 'part 6 done: household_members linked, DOB cleared, titles backfilled';
end
$$;
