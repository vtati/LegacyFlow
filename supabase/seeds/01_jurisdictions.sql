-- ============================================================================
-- LegacyFlow — Foundation Slice 1 seeds: jurisdiction reference data
--
-- Idempotent: fixed UUIDs + ON CONFLICT DO NOTHING, safe to re-run.
-- Synthetic data only. No application code may hard-code jurisdiction
-- behavior — the US-XZ synthetic pack exists precisely so tests can
-- distinguish packs by *config* (§12.2 scenario 6).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- US-VA — Virginia (real starter jurisdiction, §9.3)
-- ---------------------------------------------------------------------------
insert into jurisdictions (id, country_code, subdivision_code, display_name, jurisdiction_type, is_enabled)
values (
  '11111111-1111-1111-1111-111111111111',
  'US', 'VA', 'Virginia', 'us_state', true
)
on conflict (id) do nothing;

insert into jurisdiction_packs (
  id, jurisdiction_id, version, status, effective_from,
  manifest, source_bundle_hash
)
values (
  '22222222-2222-2222-2222-222222222222',
  '11111111-1111-1111-1111-111111111111',
  '0.1.0',
  'internal_testing',
  date '2026-09-26',
  '{
    "pack": {
      "id": "us-va",
      "country_code": "US",
      "subdivision_code": "VA",
      "display_name": "Virginia",
      "semantic_version": "0.1.0",
      "status": "internal_testing",
      "effective_from": "2026-09-26"
    },
    "governance": {
      "legal_content_owner_type": "attorney_or_firm",
      "review_status": "pending",
      "approved_by": null
    },
    "capabilities": {
      "intake": true,
      "education": true,
      "eligibility_triage": true,
      "attorney_review": true,
      "document_assembly": false,
      "execution_guidance": "attorney_pending",
      "funding_guidance": true
    },
    "requirements": {
      "minimum_platform_version": "1.0.0",
      "required_question_set_versions": {
        "household": "1.0.0",
        "assets": "1.0.0"
      }
    },
    "sources": [
      {
        "source_id": "va-code-will-execution",
        "authority_type": "statute",
        "citation": "Va. Code § 64.2-403",
        "source_url": "https://law.lis.virginia.gov/vacode/title64.2/chapter2/section64.2-403/",
        "retrieved_on": "2026-09-26"
      },
      {
        "source_id": "va-code-advance-directive",
        "authority_type": "statute",
        "citation": "Va. Code § 54.1-2983 and § 54.1-2984",
        "source_url": "https://law.lis.virginia.gov/vacode/title54.1/chapter29/section54.1-2984/",
        "retrieved_on": "2026-09-26"
      }
    ]
  }'::jsonb,
  'synthetic-dev-only'
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- US-XZ — Synthetic Xanadu (TEST ONLY)
-- Visibly different config from VA: education/eligibility_triage/funding off,
-- execution_guidance disabled, and a 9.9.9-test question set version.
-- ---------------------------------------------------------------------------
insert into jurisdictions (id, country_code, subdivision_code, display_name, jurisdiction_type, is_enabled)
values (
  '33333333-3333-3333-3333-333333333333',
  'US', 'XZ', 'Synthetic Xanadu (TEST ONLY)', 'us_state', true
)
on conflict (id) do nothing;

insert into jurisdiction_packs (
  id, jurisdiction_id, version, status, effective_from,
  manifest, source_bundle_hash
)
values (
  '44444444-4444-4444-4444-444444444444',
  '33333333-3333-3333-3333-333333333333',
  '1.0.0',
  'internal_testing',
  date '2026-09-26',
  '{
    "pack": {
      "id": "us-xz-synthetic",
      "country_code": "US",
      "subdivision_code": "XZ",
      "display_name": "Synthetic Xanadu (TEST ONLY)",
      "semantic_version": "1.0.0",
      "status": "internal_testing",
      "effective_from": "2026-09-26"
    },
    "governance": {
      "legal_content_owner_type": "attorney_or_firm",
      "review_status": "pending",
      "approved_by": null
    },
    "capabilities": {
      "intake": true,
      "education": false,
      "eligibility_triage": false,
      "attorney_review": true,
      "document_assembly": false,
      "execution_guidance": "disabled",
      "funding_guidance": false
    },
    "requirements": {
      "minimum_platform_version": "1.0.0",
      "required_question_set_versions": {
        "household": "9.9.9-test",
        "assets": "9.9.9-test"
      }
    },
    "sources": [
      {
        "source_id": "xz-synthetic-fixture",
        "authority_type": "other",
        "citation": "Synthetic fixture — no legal authority",
        "source_url": null,
        "retrieved_on": "2026-09-26"
      }
    ]
  }'::jsonb,
  'synthetic-dev-only'
)
on conflict (id) do nothing;
