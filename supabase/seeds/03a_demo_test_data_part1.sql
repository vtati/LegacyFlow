-- ============================================================================
-- 03a_demo_test_data_part1.sql — Part 1 of 4: identity, firm, households
-- ============================================================================
-- Paste into the Supabase dashboard SQL editor
-- (project nlrxlpjkrmvubvsfepjs) and Run. Repeat for parts 2–4 in order.
-- ALL DATA IS SYNTHETIC. Idempotent: safe to re-run.
-- ============================================================================

do $$
declare
  v_email  constant text := 'tati.venugopal@gmail.com';
  v_user   uuid;

  v_va_jur constant uuid := '11111111-1111-1111-1111-111111111111';
  v_va_pack constant uuid := '22222222-2222-2222-2222-222222222222';

  v_firm   constant uuid := '20000000-0000-0000-0000-000000000001';
  v_hh_a   constant uuid := '20000000-0000-0000-0000-000000000002';
  v_hh_b   constant uuid := '20000000-0000-0000-0000-000000000003';
begin
  select u.id into v_user from auth.users u where u.email = v_email;
  if v_user is null then
    raise exception 'auth user not found: % (sign in to the staging app once first)', v_email;
  end if;

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

  insert into public.profiles (id, display_name, first_name, last_name, email)
  values (v_user, 'Venugopal Tati', 'Venugopal', 'Tati', v_email)
  on conflict (id) do update set
    display_name = excluded.display_name,
    first_name   = excluded.first_name,
    last_name    = excluded.last_name,
    email        = excluded.email;

  insert into public.firms (id, legal_name, display_name, status)
  values (v_firm, 'Tati Family Law Demo PLLC', 'Tati Family Law (Demo)', 'active')
  on conflict (id) do nothing;

  insert into public.firm_memberships (firm_id, user_id, role, status)
  values (v_firm, v_user, 'firm_admin', 'active')
  on conflict (firm_id, user_id) do update set role = 'firm_admin', status = 'active';

  insert into public.households (id, firm_id, display_name, created_by_user_id)
  values
    (v_hh_a, v_firm, 'Tati Household', v_user),
    (v_hh_b, v_firm, 'Demo Household B', v_user)
  on conflict (id) do nothing;

  insert into public.household_members (household_id, user_id, relationship_role, access_status)
  values (v_hh_a, v_user, 'primary_client', 'active')
  on conflict do nothing;

  raise notice 'part 1/4 done: firm %, households % and %', v_firm, v_hh_a, v_hh_b;
end
$$;
