-- ============================================================================
-- Worker partial indexes (PERF-005 / PERF-006 / PERF-007, deep review
-- 2026-10-29)
--
-- The worker's periodic drains select small "pending" subsets of large
-- tables; partial indexes keep those scans index-only on the hot rows:
--   * file_uploads scan drain: WHERE scan_status = 'pending'
--   * notification outbox drain: WHERE status = 'pending' (the existing
--     (firm_id, status) index cannot serve a firm-agnostic drain)
--   * reminder dispatch: WHERE sent_at IS NULL AND scheduled_for <= now()
--     (also supports the suppressed-count folded into the selection)
-- ============================================================================

create index if not exists file_uploads_scan_pending_created_idx
  on file_uploads (created_at) where scan_status = 'pending';

create index if not exists notification_outbox_pending_created_idx
  on notification_outbox (created_at) where status = 'pending';

create index if not exists reminders_unsent_scheduled_idx
  on reminders (scheduled_for) where sent_at is null;
