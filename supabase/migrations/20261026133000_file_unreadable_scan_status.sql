-- OCR visibility for vault uploads (F7, 2026-10-26).
--
-- A scanned/image-only PDF previously recorded extraction_status
-- 'failed' and the UI said nothing — silent emptiness. The new
-- 'unreadable_scan' status distinguishes "parsed, but no readable
-- text (a scan the vault cannot OCR)" from a corrupt file, and the
-- file list renders an explicit "couldn't read this scan" note.
-- Constraint name verified against the production database
-- (file_uploads_extraction_status_check).

alter table file_uploads
  drop constraint if exists file_uploads_extraction_status_check;

alter table file_uploads
  add constraint file_uploads_extraction_status_check
  check (extraction_status in ('pending', 'done', 'unavailable', 'failed', 'unreadable_scan'));
