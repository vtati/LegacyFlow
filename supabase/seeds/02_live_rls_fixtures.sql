-- ============================================================================
-- 02_live_rls_fixtures.sql — RLS test fixtures for the LIVE Supabase project
-- ============================================================================
-- Run this in the Supabase dashboard SQL editor (project nlrxlpjkrmvubvsfepjs)
-- AFTER applying supabase/migrations/*.sql.
--
-- Prerequisites (one-time, in the dashboard):
--   1. Authentication > Users: create TWO users with the emails below
--      (any password you choose; "Auto Confirm User" on).
--   2. Paste this script into the SQL editor and run it.
--
-- Only the two EMAIL constants need editing. Everything else uses fixed
-- synthetic UUIDs that match supabase/tests/test_rls_live.py — do not change
-- them. All fixture data is synthetic. The script is idempotent: safe to
-- re-run (existing rows are left untouched).
--
-- What it creates:
--   * minimal US-VA jurisdiction + us-va pack rows (only if missing; the full
--     supabase/seeds/01_jurisdictions.sql may be applied separately at any time)
--   * profiles for both users
--   * two firms (Synthetic Firm A / B) with one attorney membership each
--   * one household + one matter + one audit event per firm
-- ============================================================================

do $$
declare
  -- >>>>>>>>>>>>>>>>>>>> EDIT THESE TWO EMAILS <<<<<<<<<<<<<<<<<<<<
  v_email_a constant text := 'slice1-rls-a@example.test';
  v_email_b constant text := 'slice1-rls-b@example.test';
  -- >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

  v_user_a uuid;
  v_user_b uuid;

  -- Fixed fixture UUIDs (must match supabase/tests/test_rls_live.py)
  v_firm_a      constant uuid := 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
  v_firm_b      constant uuid := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  v_household_a constant uuid := '40000000-0000-0000-0000-000000000000';
  v_household_b constant uuid := '50000000-0000-0000-0000-000000000000';
  v_matter_a    constant uuid := '60000000-0000-0000-0000-000000000000';
  v_matter_b    constant uuid := '70000000-0000-0000-0000-000000000000';
  v_va_jur      constant uuid := '11111111-1111-1111-1111-111111111111';
  v_va_pack     constant uuid := '22222222-2222-2222-2222-222222222222';
begin
  -- --- resolve the two auth users (created in the dashboard) ---
  select u.id into v_user_a from auth.users u where u.email = v_email_a;
  if v_user_a is null then
    raise exception 'auth user not found: % (create it under Authentication > Users first)', v_email_a;
  end if;
  select u.id into v_user_b from auth.users u where u.email = v_email_b;
  if v_user_b is null then
    raise exception 'auth user not found: % (create it under Authentication > Users first)', v_email_b;
  end if;

  -- --- minimal US-VA reference rows (only if the seeds were not applied) ---
  insert into public.jurisdictions (id, country_code, subdivision_code, display_name, jurisdiction_type, is_enabled)
  values (v_va_jur, 'US', 'VA', 'Virginia', 'us_state', true)
  on conflict (id) do nothing;

  insert into public.jurisdiction_packs
    (id, jurisdiction_id, version, status, effective_from, manifest, source_bundle_hash)
  values (
    v_va_pack, v_va_jur, '0.1.0', 'internal_testing', date '2026-09-26',
    '{"pack": {"id": "us-va", "note": "minimal fixture row for live RLS tests"}}',
    'fixture-no-real-bundle'
  )
  on conflict (id) do nothing;

  -- --- profiles (firm_memberships.user_id references profiles.id) ---
  insert into public.profiles (id, display_name, email)
  values
    (v_user_a, 'Synthetic Attorney A', v_email_a),
    (v_user_b, 'Synthetic Attorney B', v_email_b)
  on conflict (id) do nothing;

  -- --- firms ---
  insert into public.firms (id, legal_name, display_name, status)
  values
    (v_firm_a, 'Synthetic Firm A PLLC', 'Synthetic Firm A', 'active'),
    (v_firm_b, 'Synthetic Firm B PLLC', 'Synthetic Firm B', 'active')
  on conflict (id) do nothing;

  -- --- firm memberships (one attorney per firm) ---
  insert into public.firm_memberships (firm_id, user_id, role, status)
  values
    (v_firm_a, v_user_a, 'attorney', 'active'),
    (v_firm_b, v_user_b, 'attorney', 'active')
  on conflict (firm_id, user_id) do nothing;

  -- --- households ---
  insert into public.households (id, firm_id, display_name, created_by_user_id)
  values
    (v_household_a, v_firm_a, 'Synthetic Household A', v_user_a),
    (v_household_b, v_firm_b, 'Synthetic Household B', v_user_b)
  on conflict (id) do nothing;

  -- --- matters (one per firm) ---
  insert into public.matters
    (id, firm_id, household_id, primary_jurisdiction_id, jurisdiction_pack_id,
     plan_type, status, assigned_attorney_id)
  values
    (v_matter_a, v_firm_a, v_household_a, v_va_jur, v_va_pack, 'will', 'intake', v_user_a),
    (v_matter_b, v_firm_b, v_household_b, v_va_jur, v_va_pack, 'will', 'intake', v_user_b)
  on conflict (id) do nothing;

  -- --- audit events (one per firm; insert only once) ---
  if not exists (select 1 from public.audit_events where firm_id in (v_firm_a, v_firm_b)) then
    insert into public.audit_events
      (firm_id, matter_id, actor_user_id, actor_type, action, resource_type, resource_id)
    values
      (v_firm_a, v_matter_a, v_user_a, 'attorney', 'matter.created', 'matter', v_matter_a),
      (v_firm_b, v_matter_b, v_user_b, 'attorney', 'matter.created', 'matter', v_matter_b);
  end if;

  raise notice 'live RLS fixtures ready: firms % and %', v_firm_a, v_firm_b;
end
$$;
