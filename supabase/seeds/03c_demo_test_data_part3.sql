-- ============================================================================
-- 03c_demo_test_data_part3.sql — Part 3 of 4: matters, intake, decisions
-- ============================================================================
-- Run AFTER part 2. ALL DATA IS SYNTHETIC. Idempotent: safe to re-run.
-- ============================================================================

do $$
declare
  v_email    constant text := 'tati.venugopal@gmail.com';
  v_user     uuid;

  v_va_jur   constant uuid := '11111111-1111-1111-1111-111111111111';
  v_va_pack  constant uuid := '22222222-2222-2222-2222-222222222222';

  v_firm     constant uuid := '20000000-0000-0000-0000-000000000001';
  v_hh_a     constant uuid := '20000000-0000-0000-0000-000000000002';
  v_hh_b     constant uuid := '20000000-0000-0000-0000-000000000003';
  v_matter_1 constant uuid := '20000000-0000-0000-0000-000000000004';
  v_matter_2 constant uuid := '20000000-0000-0000-0000-000000000005';

  v_p_spouse constant uuid := '20000000-0000-0000-0000-000000000007';
  v_p_guard1 constant uuid := '20000000-0000-0000-0000-000000000010';
  v_p_guard2 constant uuid := '20000000-0000-0000-0000-000000000011';
begin
  select u.id into v_user from auth.users u where u.email = v_email;
  if v_user is null then
    raise exception 'auth user not found: % (sign in to the staging app once first)', v_email;
  end if;

  -- --- matters (jurisdiction_snapshot pins the on-disk us-va 0.1.0 pack) ---
  insert into public.matters
    (id, firm_id, household_id, primary_jurisdiction_id, jurisdiction_pack_id,
     plan_type, status, assigned_attorney_id, jurisdiction_snapshot)
  values
    (v_matter_1, v_firm, v_hh_a, v_va_jur, v_va_pack, 'will',  'INTAKE_IN_PROGRESS', v_user,
     '{"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": "0.1.0", "rule_ids": ["VA-ESC-001", "VA-ESC-002", "VA-ESC-003", "VA-ESC-004", "VA-ESC-005", "VA-ESC-006", "VA-ESC-007"], "question_set_versions": {"household": "1.0.0", "guardians": "1.0.0", "assets": "1.0.0"}}'),
    (v_matter_2, v_firm, v_hh_b, v_va_jur, v_va_pack, 'trust', 'ATTORNEY_REVIEW',    v_user,
     '{"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": "0.1.0", "rule_ids": ["VA-ESC-001", "VA-ESC-002", "VA-ESC-003", "VA-ESC-004", "VA-ESC-005", "VA-ESC-006", "VA-ESC-007"], "question_set_versions": {"household": "1.0.0", "guardians": "1.0.0", "assets": "1.0.0"}}')
  on conflict (id) do nothing;

  insert into public.matter_status_history (matter_id, from_status, to_status, reason_code, actor_user_id)
  values
    (v_matter_2, 'DRAFT', 'INTAKE_SUBMITTED', 'demo_seed', v_user),
    (v_matter_2, 'INTAKE_SUBMITTED', 'ATTORNEY_REVIEW', 'demo_seed', v_user)
  on conflict do nothing;

  -- --- intake answers (matter 1): keys match content-packs/us-va/v0 ---
  insert into public.intake_answers (matter_id, question_key, answer)
  values
    (v_matter_1, 'marital_status', '"married"'),
    (v_matter_1, 'primary_residence_state', '"VA"'),
    (v_matter_1, 'primary_guardian_person', jsonb_build_object('person_id', v_p_guard1, 'name', 'Priya Sharma')),
    (v_matter_1, 'guardian_reason', '"Priya is the children''s aunt and already plays an active role in their lives."'),
    (v_matter_1, 'backup_guardian_person', jsonb_build_object('person_id', v_p_guard2, 'name', 'David Chen')),
    (v_matter_1, 'asset_categories_present', '["real_estate", "bank_account", "retirement"]'),
    (v_matter_1, 'has_debts', 'true'),
    (v_matter_1, 'beneficiary_review_unknown_ok', 'true')
  on conflict (matter_id, question_key) do nothing;

  -- --- intake answers (matter 2, lighter) ---
  insert into public.intake_answers (matter_id, question_key, answer)
  values
    (v_matter_2, 'marital_status', '"single"'),
    (v_matter_2, 'primary_residence_state', '"VA"'),
    (v_matter_2, 'has_minor_children', 'false'),
    (v_matter_2, 'asset_categories_present', '["bank_account", "investment"]'),
    (v_matter_2, 'has_debts', 'false')
  on conflict (matter_id, question_key) do nothing;

  -- --- planning decisions (matter 1) ---
  insert into public.planning_decisions (matter_id, decision_key, value, decided_by)
  values
    (v_matter_1, 'guardian_strategy', '{"primary": "Priya Sharma", "backup": "David Chen"}', v_user),
    (v_matter_1, 'executor_choice',   '{"executor": "Maya Tati"}', v_user)
  on conflict (matter_id, decision_key) do nothing;

  -- --- nominations (matter 1; guarded for idempotent re-runs) ---
  insert into public.nominations (matter_id, nomination_type, person_id, priority, notes, status)
  select v_matter_1, 'guardian', v_p_guard1, 1, 'Maternal aunt; confirmed verbally.', 'confirmed'
  where not exists (select 1 from public.nominations
                   where matter_id = v_matter_1 and nomination_type = 'guardian'
                     and person_id = v_p_guard1 and priority = 1);
  insert into public.nominations (matter_id, nomination_type, person_id, priority, notes, status)
  select v_matter_1, 'guardian', v_p_guard2, 2, 'Family friend; backup guardian.', 'proposed'
  where not exists (select 1 from public.nominations
                   where matter_id = v_matter_1 and nomination_type = 'guardian'
                     and person_id = v_p_guard2 and priority = 2);
  insert into public.nominations (matter_id, nomination_type, person_id, priority, notes, status)
  select v_matter_1, 'executor', v_p_spouse, 1, 'Spouse named as executor.', 'confirmed'
  where not exists (select 1 from public.nominations
                   where matter_id = v_matter_1 and nomination_type = 'executor'
                     and person_id = v_p_spouse);
  insert into public.nominations (matter_id, nomination_type, person_id, priority, notes, status)
  select v_matter_1, 'trustee',  v_p_spouse, 1, 'Spouse named as successor trustee.', 'proposed'
  where not exists (select 1 from public.nominations
                   where matter_id = v_matter_1 and nomination_type = 'trustee'
                     and person_id = v_p_spouse);

  raise notice 'part 3/4 done: matters % and % with intake + nominations', v_matter_1, v_matter_2;
end
$$;
