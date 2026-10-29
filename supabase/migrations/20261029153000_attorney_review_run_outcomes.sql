-- ============================================================================
-- Attorney Reviewer run outcomes (ERR-019 / ERR-020, deep review 2026-10-29)
--
--   degraded         — the model layer did not complete for this run
--                      (provider failure degrading to deterministic-only,
--                      or unparseable model output). Lets the API surface
--                      "the judgment pass hasn't really run" instead of
--                      rendering a degraded run as a clean full review.
--   skipped_findings — findings whose insert failed inside
--                      _persist_review and were skipped via per-finding
--                      savepoints (ERR-019) instead of sinking the run.
--
-- attorney_review_runs already has RLS enabled (migration
-- 20261027160000); no new table is created here.
-- ============================================================================

alter table attorney_review_runs
  add column if not exists degraded boolean not null default false;

alter table attorney_review_runs
  add column if not exists skipped_findings integer not null default 0;
