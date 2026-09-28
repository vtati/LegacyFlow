-- ============================================================================
-- 03b_demo_test_data_part2.sql — Part 2 of 4: persons + relationships
-- ============================================================================
-- Run AFTER part 1. ALL DATA IS SYNTHETIC. Idempotent: safe to re-run.
-- ============================================================================

do $$
declare
  v_email    constant text := 'tati.venugopal@gmail.com';

  v_hh_a     constant uuid := '20000000-0000-0000-0000-000000000002';
  v_hh_b     constant uuid := '20000000-0000-0000-0000-000000000003';

  v_p_user   constant uuid := '20000000-0000-0000-0000-000000000006';
  v_p_spouse constant uuid := '20000000-0000-0000-0000-000000000007';
  v_p_kid1   constant uuid := '20000000-0000-0000-0000-000000000008';
  v_p_kid2   constant uuid := '20000000-0000-0000-0000-000000000009';
  v_p_guard1 constant uuid := '20000000-0000-0000-0000-000000000010';
  v_p_guard2 constant uuid := '20000000-0000-0000-0000-000000000011';
  v_p_b1     constant uuid := '20000000-0000-0000-0000-000000000012';
begin
  -- --- persons (household A; all names except the account holder synthetic) ---
  insert into public.persons (id, household_id, full_name, date_of_birth, email, kind, notes)
  values
    (v_p_user,   v_hh_a, 'Venugopal Tati', date '1985-06-15', v_email, 'adult',
     'Primary client (staging test account).'),
    (v_p_spouse, v_hh_a, 'Maya Tati', date '1987-03-22', null, 'adult',
     'Spouse of primary client. Synthetic demo record.'),
    (v_p_kid1,   v_hh_a, 'Arjun Tati', date '2014-09-10', null, 'minor',
     'Minor child. Synthetic demo record.'),
    (v_p_kid2,   v_hh_a, 'Sara Tati', date '2018-02-28', null, 'minor',
     'Minor child. Synthetic demo record.'),
    (v_p_guard1, v_hh_a, 'Priya Sharma', date '1983-11-02', null, 'adult',
     'Maternal aunt of the minor children; nominated guardian (primary). Does not reside in household. Synthetic demo record.'),
    (v_p_guard2, v_hh_a, 'David Chen', date '1980-07-19', null, 'adult',
     'Family friend; nominated guardian (backup). Synthetic demo record.')
  on conflict (id) do nothing;

  insert into public.persons (id, household_id, full_name, date_of_birth, email, kind, notes)
  values
    (v_p_b1, v_hh_b, 'Jordan Blake', date '1979-01-30', null, 'adult',
     'Primary client of Demo Household B. Synthetic demo record.')
  on conflict (id) do nothing;

  -- --- relationships (household A; guarded for idempotent re-runs) ---
  insert into public.relationships (household_id, from_person_id, to_person_id, relationship_type)
  select v_hh_a, v_p_user,   v_p_spouse, 'spouse'
  where not exists (select 1 from public.relationships
                   where household_id = v_hh_a and from_person_id = v_p_user
                     and to_person_id = v_p_spouse and relationship_type = 'spouse');
  insert into public.relationships (household_id, from_person_id, to_person_id, relationship_type)
  select v_hh_a, v_p_spouse, v_p_user,   'spouse'
  where not exists (select 1 from public.relationships
                   where household_id = v_hh_a and from_person_id = v_p_spouse
                     and to_person_id = v_p_user and relationship_type = 'spouse');
  insert into public.relationships (household_id, from_person_id, to_person_id, relationship_type)
  select v_hh_a, v_p_user,   v_p_kid1,   'parent'
  where not exists (select 1 from public.relationships
                   where household_id = v_hh_a and from_person_id = v_p_user
                     and to_person_id = v_p_kid1 and relationship_type = 'parent');
  insert into public.relationships (household_id, from_person_id, to_person_id, relationship_type)
  select v_hh_a, v_p_spouse, v_p_kid1,   'parent'
  where not exists (select 1 from public.relationships
                   where household_id = v_hh_a and from_person_id = v_p_spouse
                     and to_person_id = v_p_kid1 and relationship_type = 'parent');
  insert into public.relationships (household_id, from_person_id, to_person_id, relationship_type)
  select v_hh_a, v_p_user,   v_p_kid2,   'parent'
  where not exists (select 1 from public.relationships
                   where household_id = v_hh_a and from_person_id = v_p_user
                     and to_person_id = v_p_kid2 and relationship_type = 'parent');
  insert into public.relationships (household_id, from_person_id, to_person_id, relationship_type)
  select v_hh_a, v_p_spouse, v_p_kid2,   'parent'
  where not exists (select 1 from public.relationships
                   where household_id = v_hh_a and from_person_id = v_p_spouse
                     and to_person_id = v_p_kid2 and relationship_type = 'parent');
  insert into public.relationships (household_id, from_person_id, to_person_id, relationship_type)
  select v_hh_a, v_p_guard1, v_p_kid1,   'aunt'
  where not exists (select 1 from public.relationships
                   where household_id = v_hh_a and from_person_id = v_p_guard1
                     and to_person_id = v_p_kid1 and relationship_type = 'aunt');
  insert into public.relationships (household_id, from_person_id, to_person_id, relationship_type)
  select v_hh_a, v_p_guard1, v_p_kid2,   'aunt'
  where not exists (select 1 from public.relationships
                   where household_id = v_hh_a and from_person_id = v_p_guard1
                     and to_person_id = v_p_kid2 and relationship_type = 'aunt');

  raise notice 'part 2/4 done: 7 persons + relationships';
end
$$;
