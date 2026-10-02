-- Supabase Storage buckets for LegacyFlow document storage (Sprint: storage migration).
--
-- Two private buckets:
--   client-uploads: user-uploaded files (via the files domain)
--   plan-documents: server-generated documents (via the documents domain)
--
-- Buckets are PRIVATE (public=false). No storage.objects RLS policies are
-- granted to authenticated users — all access goes through the API, which
-- enforces firm/household scope before generating signed URLs. The service
-- role bypasses RLS for backend operations.

INSERT INTO storage.buckets (id, name, public)
VALUES
  ('client-uploads', 'client-uploads', false),
  ('plan-documents', 'plan-documents', false)
ON CONFLICT (id) DO NOTHING;
