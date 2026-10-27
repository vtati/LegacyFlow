-- Family self-serve draft generation (2026-10-26 plan, D4):
-- document_packages.source_fingerprint stores the sha256 of the exact
-- user-editable inputs the renderer consumed at render time (answers,
-- persons, nominations, matter title, household name). A package whose
-- stored fingerprint differs from the current source data is STALE:
-- the family must regenerate before submitting for attorney review.
-- Nullable: packages rendered before this migration have no fingerprint;
-- in the family draft-set state a package WITH demo versions but a NULL
-- (or mismatched) fingerprint reads as STALE (stored != current), so the
-- family regenerates once and the stamp is written.
ALTER TABLE document_packages ADD COLUMN IF NOT EXISTS source_fingerprint text;

COMMENT ON COLUMN document_packages.source_fingerprint IS
  'sha256 hex of canonical JSON over the user-editable render inputs captured at render time; NULL = never rendered by the fingerprinted render core.';
