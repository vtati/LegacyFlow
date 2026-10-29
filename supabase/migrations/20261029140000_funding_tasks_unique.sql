-- FUNC-013 (2026-10-29): funding-task generation was check-then-insert
-- with no database uniqueness on (matter_id, asset_id, task_type) —
-- two concurrent generations could both pass the existence check and
-- insert duplicate tasks.
--
-- Production duplicate check before applying (2026-10-29, Management
-- SQL): zero duplicate (matter_id, asset_id, task_type) groups in
-- funding_tasks (3 tasks total, none with a NULL asset_id), so the
-- index builds cleanly with no data changes.
--
-- asset_id is nullable (matter-level tasks), and plain UNIQUE treats
-- NULLs as distinct — the index therefore uses NULLS NOT DISTINCT
-- (PostgreSQL 15+; production verified PostgreSQL 17.6) so NULL-asset
-- tasks are deduplicated too. The repository insert now runs
-- ON CONFLICT DO NOTHING against this guard.
-- Backend index only; funding_tasks already has RLS enabled and an
-- index needs no policy.

create unique index uq_funding_tasks_matter_asset_type
  on funding_tasks (matter_id, asset_id, task_type) nulls not distinct;
