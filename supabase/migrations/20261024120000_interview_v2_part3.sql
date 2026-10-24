-- Interview v2 Part 3: bundle-offer state on interview sessions, and the
-- 'bundle' source for server-created role-bundle proposals.
-- interview_sessions already has RLS enabled; this only adds a column.
alter table interview_sessions
  add column if not exists state jsonb not null default '{}'::jsonb;

alter table field_proposals
  drop constraint if exists field_proposals_source_check;
alter table field_proposals
  add constraint field_proposals_source_check
  check (source in ('user_stated', 'document_extracted', 'ai_inferred', 'bundle'));
