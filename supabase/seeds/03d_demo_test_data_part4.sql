-- ============================================================================
-- 03d_demo_test_data_part4.sql — Part 4 of 5: assets, liabilities, funding
-- ============================================================================
-- Run AFTER part 3. ALL DATA IS SYNTHETIC. Idempotent: safe to re-run.
-- ============================================================================

do $$
declare
  v_email    constant text := 'tati.venugopal@gmail.com';
  v_user     uuid;

  v_firm     constant uuid := '20000000-0000-0000-0000-000000000001';
  v_hh_a     constant uuid := '20000000-0000-0000-0000-000000000002';
  v_matter_1 constant uuid := '20000000-0000-0000-0000-000000000004';

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
  v_flag1      constant uuid := '20000000-0000-0000-0000-000000000038';
begin
  select u.id into v_user from auth.users u where u.email = v_email;
  if v_user is null then
    raise exception 'auth user not found: % (sign in to the staging app once first)', v_email;
  end if;

  -- --- assets (matter 1; values in cents) ---
  insert into public.assets
    (id, firm_id, household_id, matter_id, asset_type, label, estimated_value_cents,
     ownership, beneficiary_designation, notes, created_by_user_id)
  values
    (v_asset_house, v_firm, v_hh_a, v_matter_1, 'real_estate', 'Primary residence — Ashburn, VA',
     65000000, 'joint', '{"primary": [], "contingent": []}',
     'Single-family home purchased 2019. Synthetic demo record.', v_user),
    (v_asset_check, v_firm, v_hh_a, v_matter_1, 'bank_account', 'Joint checking — First Bank',
     2500000, 'joint', '{"primary": [], "contingent": []}', 'Everyday checking. Synthetic demo record.', v_user),
    (v_asset_save, v_firm, v_hh_a, v_matter_1, 'bank_account', 'Emergency savings — First Bank',
     8000000, 'joint', '{"primary": [], "contingent": []}', 'High-yield savings. Synthetic demo record.', v_user),
    (v_asset_401k, v_firm, v_hh_a, v_matter_1, 'retirement', '401(k) — Venugopal',
     21000000, 'individual', '{"primary": [{"name": "Maya Tati", "relationship": "spouse"}], "contingent": []}',
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

  raise notice 'part 4/5 done: assets, liabilities, funding tasks';
end
$$;
