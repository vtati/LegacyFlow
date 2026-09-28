-- ============================================================================
-- 03_demo_test_data.sql — Full synthetic demo environment for the staging app
-- ============================================================================
-- Run this in the Supabase dashboard SQL editor (project nlrxlpjkrmvubvsfepjs)
-- AFTER applying supabase/migrations/*.sql.
--
-- Prerequisites: the auth user below must already exist
--   (tati.venugopal@gmail.com — the staging test account).
--
-- ALL DATA IS SYNTHETIC. Names other than the test account holder are
-- invented for demo purposes. The script is idempotent: safe to re-run
-- (existing rows are left untouched).
--
-- What it creates:
--   * profile for the test user
--   * one firm ("Tati Family Law Demo PLLC") with the user as firm_admin
--   * "Tati Household" with 6 synthetic persons (2 adults, 2 minors,
--     2 nominated guardians) + relationships
--   * Matter 1 (will, INTAKE_IN_PROGRESS): full intake answers, decisions,
--     nominations, assets, liabilities, funding tasks, one escalation
--   * "Demo Household B" with Matter 2 (trust, ATTORNEY_REVIEW): approved
--     intake gate, review packet v1, comments, an open clarification, an
--     escalation — so the firm Review Queue shows real content
--   * notification preferences for the test user
-- ============================================================================

do $$
declare
  v_email      constant text := 'tati.venugopal@gmail.com';
  v_user       uuid;

  v_va_jur     constant uuid := '11111111-1111-1111-1111-111111111111';
  v_va_pack    constant uuid := '22222222-2222-2222-2222-222222222222';

  v_firm       constant uuid := '20000000-0000-0000-0000-000000000001';
  v_hh_a       constant uuid := '20000000-0000-0000-0000-000000000002';
  v_hh_b       constant uuid := '20000000-0000-0000-0000-000000000003';
  v_matter_1   constant uuid := '20000000-0000-0000-0000-000000000004';
  v_matter_2   constant uuid := '20000000-0000-0000-0000-000000000005';

  v_p_user     constant uuid := '20000000-0000-0000-0000-000000000006';
  v_p_spouse   constant uuid := '20000000-0000-0000-0000-000000000007';
  v_p_kid1     constant uuid := '20000000-0000-0000-0000-000000000008';
  v_p_kid2     constant uuid := '20000000-0000-0000-0000-000000000009';
  v_p_guard1   constant uuid := '20000000-0000-0000-0000-000000000010';
  v_p_guard2   constant uuid := '20000000-0000-0000-0000-000000000011';
  v_p_b1       constant uuid := '20000000-0000-0000-0000-000000000012';

  v_asset_house constant uuid := '20000000-0000-0000-0000-000000000020';
  v_asset_check constant uuid := '20000000-0000-0000-0000-000000000021';
  v_asset_save  constant uuid := '20000000-0000-0000-0000-000000000022';
  v_asset_401k  constant uuid := '20000000-0000-0000-0000-000000000023';

  v_lia_mort   constant uuid := '20000000-0000-0000-0000-000000000030';
  v_lia_auto   constant uuid := '20000000-0000-0000-0000-000000000031';
  v_ft1        constant uuid := '20000000-0000-0000-0000-000000000032';
  v_ft2        constant uuid := '20000000-0000-0000-0000-000000000033';
  v_ft3        constant uuid := '20000000-0000-0000-0000-000000000034';
  v_esc1       constant uuid := '20000000-0000-0000-0000-000000000035';
  v_esc2       constant uuid := '20000000-0000-0000-0000-000000000036';
  v_gate2      constant uuid := '20000000-0000-0000-0000-000000000037';
  v_flag1      constant uuid := '20000000-0000-0000-0000-000000000038';
  v_cmt1       constant uuid := '20000000-0000-0000-0000-000000000039';
  v_cmt2       constant uuid := '20000000-0000-0000-0000-000000000040';
  v_clar1      constant uuid := '20000000-0000-0000-0000-000000000041';
begin
  -- --- resolve the auth user ---
  select u.id into v_user from auth.users u where u.email = v_email;
  if v_user is null then
    raise exception 'auth user not found: % (sign in to the staging app once first)', v_email;
  end if;

  -- --- minimal US-VA reference rows (only if the seeds were not applied) ---
  insert into public.jurisdictions (id, country_code, subdivision_code, display_name, jurisdiction_type, is_enabled)
  values (v_va_jur, 'US', 'VA', 'Virginia', 'us_state', true)
  on conflict (id) do nothing;

  insert into public.jurisdiction_packs
    (id, jurisdiction_id, version, status, effective_from, manifest, source_bundle_hash)
  values (
    v_va_pack, v_va_jur, '0.1.0', 'approved', current_date - 30,
    '{"pack": {"id": "us-va", "country_code": "US", "subdivision_code": "VA", "display_name": "Virginia", "semantic_version": "0.1.0", "status": "approved"}}'::jsonb,
    'demo-seed-bundle-hash'
  )
  on conflict (id) do nothing;

  -- --- profile ---
  insert into public.profiles (id, display_name, first_name, last_name, email)
  values (v_user, 'Venugopal Tati', 'Venugopal', 'Tati', v_email)
  on conflict (id) do update set
    display_name = excluded.display_name,
    first_name   = excluded.first_name,
    last_name    = excluded.last_name,
    email        = excluded.email;

  -- --- firm + membership (firm_admin so every section is testable) ---
  insert into public.firms (id, legal_name, display_name, status)
  values (v_firm, 'Tati Family Law Demo PLLC', 'Tati Family Law (Demo)', 'active')
  on conflict (id) do nothing;

  insert into public.firm_memberships (firm_id, user_id, role, status)
  values (v_firm, v_user, 'firm_admin', 'active')
  on conflict (firm_id, user_id) do update set role = 'firm_admin', status = 'active';

  -- --- households ---
  insert into public.households (id, firm_id, display_name, created_by_user_id)
  values
    (v_hh_a, v_firm, 'Tati Household', v_user),
    (v_hh_b, v_firm, 'Demo Household B', v_user)
  on conflict (id) do nothing;

  insert into public.household_members (household_id, user_id, relationship_role, access_status)
  values (v_hh_a, v_user, 'primary_client', 'active')
  on conflict do nothing;

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
    (v_matter_1, 'has_minor_children', 'true'),
    (v_matter_1, 'has_child_with_special_needs', 'false'),
    (v_matter_1, 'has_prior_marriage_or_children_outside_current_relationship', 'false'),
    (v_matter_1, 'owns_real_estate_outside_primary_jurisdiction', 'false'),
    (v_matter_1, 'owns_or_controls_business_interest', 'false'),
    (v_matter_1, 'expects_family_conflict', 'false'),
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

  -- --- assets (matter 1; values in cents) ---
  insert into public.assets
    (id, firm_id, household_id, matter_id, asset_type, label, estimated_value_cents,
     ownership, beneficiary_designation, notes, created_by_user_id)
  values
    (v_asset_house, v_firm, v_hh_a, v_matter_1, 'real_estate', 'Primary residence — Ashburn, VA',
     65000000, 'joint', '{}',
     'Single-family home purchased 2019. Synthetic demo record.', v_user),
    (v_asset_check, v_firm, v_hh_a, v_matter_1, 'bank_account', 'Joint checking — First Bank',
     2500000, 'joint', '{}', 'Everyday checking. Synthetic demo record.', v_user),
    (v_asset_save, v_firm, v_hh_a, v_matter_1, 'bank_account', 'Emergency savings — First Bank',
     8000000, 'joint', '{}', 'High-yield savings. Synthetic demo record.', v_user),
    (v_asset_401k, v_firm, v_hh_a, v_matter_1, 'retirement', '401(k) — Venugopal',
     21000000, 'individual', '{"primary": "Maya Tati"}',
     'Employer 401(k). No contingent beneficiary named yet. Synthetic demo record.', v_user)
  on conflict (id) do nothing;

  insert into public.asset_flags (id, firm_id, asset_id, flag_type, severity, message)
  values
    (v_flag1, v_firm, v_asset_401k, 'missing_contingent', 'review',
     'No contingent beneficiary named on the 401(k).')
  on conflict (id) do nothing;

  -- --- liabilities (matter 1) ---
  insert into public.liabilities
    (id, firm_id, household_id, matter_id, liability_type, label, balance_cents,
     monthly_payment_cents, notes, created_by_user_id)
  values
    (v_lia_mort, v_firm, v_hh_a, v_matter_1, 'mortgage', 'Primary mortgage — First Bank',
     42000000, 285000, '30-year fixed at 6.1%. Synthetic demo record.', v_user),
    (v_lia_auto, v_firm, v_hh_a, v_matter_1, 'loan', 'Auto loan — Credit Union',
     1800000, 42000, 'Synthetic demo record.', v_user)
  on conflict (id) do nothing;

  -- --- funding tasks (matter 1) ---
  insert into public.funding_tasks
    (id, firm_id, matter_id, asset_id, task_type, title, status, due_date)
  values
    (v_ft1, v_firm, v_matter_1, v_asset_401k, 'beneficiary_update',
     'Add a contingent beneficiary to the 401(k)', 'pending', current_date + 30),
    (v_ft2, v_firm, v_matter_1, v_asset_house, 'deed_transfer',
     'Prepare deed transfer for the primary residence', 'in_progress', current_date + 45),
    (v_ft3, v_firm, v_matter_1, v_asset_check, 'account_titling',
     'Retitle joint checking into the trust', 'pending', current_date + 60)
  on conflict (id) do nothing;

  -- --- escalation (matter 1) ---
  insert into public.escalations (id, matter_id, rule_id, severity, status, facts_snapshot)
  values
    (v_esc1, v_matter_1, 'missing_contingent_beneficiary', 'review', 'open',
     '{"asset": "401(k) — Venugopal", "issue": "no contingent beneficiary"}')
  on conflict (id) do nothing;

  -- --- matter 2: approved intake gate + review packet v1 + discussion ---
  insert into public.approval_gates
    (id, firm_id, matter_id, gate_type, status, decided_by_user_id, decided_at, notes)
  values
    (v_gate2, v_firm, v_matter_2, 'intake_approval', 'approved', v_user, now() - interval '2 days',
     'Intake complete and verified; ready for document assembly.')
  on conflict (id) do nothing;

  insert into public.review_packets (firm_id, matter_id, version, snapshot, created_by_user_id)
  values
    (v_firm, v_matter_2, 1,
     '{"plan_type": "trust", "note": "Seeded demo packet for review-queue testing",
       "intake_answers": {"marital_status": "single", "has_minor_children": false},
       "asset_count": 0, "flag_count": 0}'::jsonb,
     v_user)
  on conflict (matter_id, version) do nothing;

  insert into public.comments (id, firm_id, matter_id, author_user_id, body)
  values
    (v_cmt1, v_firm, v_matter_2, v_user,
     'Intake looks complete. One open question on the successor trustee before we assemble.'),
    (v_cmt2, v_firm, v_matter_2, v_user,
     'Reminder: confirm funding playbook for the investment accounts once the trust is signed.')
  on conflict (id) do nothing;

  insert into public.clarification_requests
    (id, firm_id, matter_id, question, status, created_by_user_id)
  values
    (v_clar1, v_firm, v_matter_2, 'Please confirm the successor trustee''s full legal name as it should appear in the trust.',
     'open', v_user)
  on conflict (id) do nothing;

  insert into public.escalations (id, matter_id, rule_id, severity, status, facts_snapshot)
  values
    (v_esc2, v_matter_2, 'successor_trustee_unconfirmed', 'review', 'open',
     '{"issue": "successor trustee legal name not yet confirmed"}')
  on conflict (id) do nothing;

  -- --- notification preferences ---
  insert into public.notification_preferences (user_id, email_enabled, reminder_enabled)
  values (v_user, true, true)
  on conflict (user_id) do nothing;

  -- --- audit events ---
  insert into public.audit_events
    (firm_id, matter_id, actor_user_id, actor_type, action, resource_type, resource_id)
  select v_firm, v_matter_1, v_user, 'staff', 'matter.created', 'matter', v_matter_1
  where not exists (
    select 1 from public.audit_events
    where firm_id = v_firm and action = 'matter.created' and resource_id = v_matter_1
  );

  insert into public.audit_events
    (firm_id, matter_id, actor_user_id, actor_type, action, resource_type, resource_id)
  select v_firm, v_matter_2, v_user, 'staff', 'matter.created', 'matter', v_matter_2
  where not exists (
    select 1 from public.audit_events
    where firm_id = v_firm and action = 'matter.created' and resource_id = v_matter_2
  );

  raise notice 'demo test data ready for % (firm %, matters % and %)',
    v_email, v_firm, v_matter_1, v_matter_2;
end
$$;
