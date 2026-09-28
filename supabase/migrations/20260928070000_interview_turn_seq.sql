-- ============================================================================
-- LegacyFlow — AI Interviewer (Phase 1)
-- Migration: 20260928070000_interview_turn_seq
--
-- Add a monotonic sequence to ``interview_turns`` so turn order is
-- deterministic inside a transaction. ``created_at`` uses ``now()`` which is
-- the transaction start time — the user turn and its assistant reply share
-- an identical timestamp, so ordering by ``created_at`` alone is ambiguous.
-- The sequence backs idempotent replay (find the assistant reply that
-- followed a given user turn) and stable history ordering.
-- ============================================================================

alter table interview_turns add column seq bigserial;

-- Existing rows (if any) receive sequence values in an unspecified but fixed
-- order; all future inserts are strictly increasing.
create unique index uq_interview_turns_seq on interview_turns (seq);
create index idx_interview_turns_session_seq on interview_turns (session_id, seq);
