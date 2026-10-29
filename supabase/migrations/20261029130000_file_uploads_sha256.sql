-- PERF-003 (2026-10-29): listing funding tasks downloaded every evidence
-- file's bytes on every call just to compute its SHA-256 digest.
--
-- Store the digest on file_uploads at upload-completion time (the bytes
-- are already in hand on every completion path: files-domain
-- complete_upload, document render versions, funding-task evidence).
-- The funding-task list path reads the stored digest and never
-- downloads; legacy rows keep NULL and the API surfaces no digest for
-- them (the field is display-only).
--
-- Backend column only; file_uploads already has RLS enabled and a
-- nullable column needs no policy change.

alter table file_uploads
  add column if not exists sha256 text;
