-- ============================================================================
-- 03e_demo_test_data_part5.sql — Part 5 of 5: review queue, prefs, audit
-- ============================================================================
-- Run AFTER part 4. ALL DATA IS SYNTHETIC. Idempotent: safe to re-run.
-- ============================================================================

do $$
declare
  v_email    constant text := 'tati.venugopal@gmail.com';
  v_user     uuid;

  v_firm     constant uuid := '20000000-0000-0000-0000-000000000001';
  v_matter_1 constant uuid := '20000000-0000-0000-0000-000000000004';
  v_matter_2 constant uuid := '20000000-0000-0000-0000-000000000005';

  v_esc2       constant uuid := '20000000-0000-0000-0000-000000000036';
  v_gate2      constant uuid := '20000000-0000-0000-0000-000000000037';
  v_cmt1       constant uuid := '20000000-0000-0000-0000-000000000039';
  v_cmt2       constant uuid := '20000000-0000-0000-0000-000000000040';
  v_clar1      constant uuid := '20000000-0000-0000-0000-000000000041';
begin
  select u.id into v_user from auth.users u where u.email = v_email;
  if v_user is null then
    raise exception 'auth user not found: % (sign in to the staging app once first)', v_email;
  end if;

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

  raise notice 'part 5/5 done: review queue, preferences, audit events — demo data complete';
end
$$;
