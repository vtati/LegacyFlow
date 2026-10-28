-- Consistency reviewer severity vocabulary (2026-10-27 production 500).
--
-- The interview consistency reviewer's system prompt asks its model
-- for severities 'review' and 'attention', and its parser accepts
-- exactly those two (benchmark runs returned 'attention' for most
-- genuine findings). But escalations_severity_check — written in the
-- slice-2 migration, before the consistency feature existed — only
-- allows ('info', 'review', 'blocking'). The first production review
-- that found a real contradiction and persisted its escalation hit a
-- CHECK violation inside the request transaction and the endpoint
-- returned 500, rolling back the whole review.
--
-- 'attention' slots between 'review' and 'blocking': it carries no
-- gating semantics ('blocking' remains the rules engine's alone — the
-- matter ESCALATED transition keys off pack rule severities, not these
-- rows), the API serializes escalation severity as a plain string, and
-- the web severity type is open-ended. Constraint name verified
-- against the production database (escalations_severity_check).

alter table escalations
  drop constraint if exists escalations_severity_check;

alter table escalations
  add constraint escalations_severity_check
  check (severity in ('info', 'review', 'attention', 'blocking'));
