-- FUNC-020 (2026-10-29): self-serve household find-or-create race.
--
-- find_or_create_self_serve_household was check-then-insert guarded only
-- by the plain (non-unique) households_self_serve_lookup_idx: two
-- concurrent create_matter calls (double-submit, two tabs, retry) both
-- saw no row and both inserted, splitting one user across two
-- self-serve households under the same firm.
--
-- This partial unique index makes (created_by_user_id, firm_id) unique
-- among self-serve households; the repository now inserts with
-- ON CONFLICT (created_by_user_id, firm_id) WHERE origin = 'self_serve'
-- DO NOTHING and re-SELECTs, so the race loser reuses the winner's
-- household. The predicate must match the ON CONFLICT target exactly.
--
-- Production duplicate check before applying (2026-10-29, Management
-- SQL): zero duplicate (created_by_user_id, firm_id) groups among
-- households WHERE origin = 'self_serve' (1 self-serve household
-- total), so the index builds cleanly with no data changes.
-- Backend table change only; households already has RLS enabled and an
-- index needs no policy.

create unique index households_self_serve_unique_idx
  on households (created_by_user_id, firm_id)
  where origin = 'self_serve';
