-- ============================================================================
-- LegacyFlow — Files & Documents Slice 4
-- Migration: 20260927030000_files_documents_slice4
--
-- Extends the Slice 2 household-intake and Slice 3 assets-and-review domains
-- with the files-and-documents domain: new tables file_uploads,
-- document_templates, document_packages, document_versions,
-- signing_checklists, funding_tasks, vault_shares, ai_interactions,
-- reminders — all with Row Level Security reusing the Slice 1
-- security-definer helpers (public.is_firm_member, public.is_firm_admin,
-- public.is_household_member, public.household_firm_id) and the
-- public.set_updated_at() trigger helper.
--
-- Conventions (§7.1, carried over from Slice 1/2/3):
--   * UUID primary keys, gen_random_uuid() (core since PG13).
--   * updated_at maintained by trigger on tables that carry the column.
--   * firm tenancy enforced via RLS; the API service role bypasses RLS and
--     enforces authorization server-side (app/core/authz.py, app/core/audit.py).
--
-- Privacy note: ai_interactions stores question_summary / answer_summary
-- and a trace_hash only — raw user prompts and raw model responses are
-- never persisted (see §8g comment).
--
-- Local test note: like Slice 1/2/3, this migration is validated against a
-- local pgserver instance whose bundled Postgres lacks contrib extension
-- files, so the citext install below is guarded the same way. Slice 4
-- tables do not use citext themselves; the guard only keeps the migration
-- self-contained when applied on a fresh database without Slices 1–3.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 0. citext extension (guarded: absent from the local pgserver test bundle)
-- ---------------------------------------------------------------------------
do $citext_guard$
begin
  create extension if not exists citext;
exception when sqlstate '0A000' then
  raise notice 'citext extension files unavailable in this environment; skipping (Slice 4 tables do not depend on citext).';
end
$citext_guard$;

-- ---------------------------------------------------------------------------
-- 1. New tables
-- ---------------------------------------------------------------------------

create table file_uploads (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  household_id uuid not null references households(id) on delete cascade,
  matter_id uuid references matters(id) on delete set null,
  uploader_user_id uuid not null references profiles(id),
  original_filename text not null,
  stored_path text not null,
  mime_type text,
  size_bytes bigint check (size_bytes >= 0),
  scan_status text not null default 'pending' check (scan_status in (
    'pending','clean','quarantined'
  )),
  scan_detail text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table document_templates (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  name text not null,
  template_type text not null check (template_type in (
    'will','trust','poa','instruction_letter'
  )),
  pack_id text,
  version text not null default '1.0.0',
  content jsonb not null default '{}'::jsonb,
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table document_packages (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  template_id uuid not null references document_templates(id),
  status text not null default 'draft' check (status in (
    'draft','demo_released'
  )),
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Immutable once created: no UPDATE/DELETE client policies below; rows are
-- only ever appended (versioned renders).
create table document_versions (
  id uuid primary key default gen_random_uuid(),
  package_id uuid not null references document_packages(id) on delete cascade,
  version integer not null check (version > 0),
  file_upload_id uuid references file_uploads(id) on delete set null,
  watermark_text text,
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now(),
  unique (package_id, version)
);

create table signing_checklists (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  checklist jsonb not null default '{}'::jsonb,
  completed_at timestamptz,
  completed_by_user_id uuid references profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table funding_tasks (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  asset_id uuid references assets(id) on delete set null,
  task_type text not null,
  title text not null,
  status text not null default 'pending' check (status in (
    'pending','in_progress','completed'
  )),
  evidence_file_id uuid references file_uploads(id) on delete set null,
  due_date date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table vault_shares (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  token_hash text not null unique,
  label text not null default '',
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_by_user_id uuid not null references profiles(id),
  created_at timestamptz not null default now()
);

-- Summaries only: question_summary / answer_summary are distilled by the
-- application before insert; raw user prompts and raw model responses are
-- never stored in this table. Citations and trace_hash support audit.
create table ai_interactions (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid references matters(id) on delete set null,
  user_id uuid not null references profiles(id),
  question_summary text not null,
  answer_summary text not null,
  citations jsonb not null default '[]'::jsonb,
  trace_hash text,
  created_at timestamptz not null default now()
);

create table reminders (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firms(id) on delete cascade,
  matter_id uuid references matters(id) on delete set null,
  user_id uuid not null references profiles(id),
  reminder_type text not null,
  scheduled_for timestamptz not null,
  sent_at timestamptz,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 2. Guards: household clients may only touch completion/evidence fields.
--
-- signing_checklists and funding_tasks grant household members an UPDATE
-- policy (see §4e/4f). These triggers narrow that path so the firm-authored
-- content stays immutable for the household path:
--   * signing_checklists: only completed_at / completed_by_user_id may change.
--   * funding_tasks: only status / evidence_file_id may change.
-- Firm members (and the service role / owner, for whom auth.uid() is null)
-- are unrestricted. Mirrors the Slice 3 clarification-request guard.
-- ---------------------------------------------------------------------------
create or replace function public.guard_signing_checklist_update()
returns trigger
language plpgsql
as $$
begin
  -- service role / owner path: unrestricted
  if auth.uid() is null then
    return NEW;
  end if;
  -- firm member path: unrestricted (RLS already limited the row to their firm)
  if public.is_firm_member(OLD.firm_id) then
    return NEW;
  end if;
  -- household path: only the completion fields may change
  if (NEW.firm_id, NEW.matter_id, NEW.checklist)
     is distinct from
     (OLD.firm_id, OLD.matter_id, OLD.checklist) then
    raise exception 'household members may only update completion fields on signing checklists';
  end if;
  return NEW;
end;
$$;

create trigger trg_signing_checklists_household_update_guard
before update on signing_checklists
for each row execute function public.guard_signing_checklist_update();

create or replace function public.guard_funding_task_update()
returns trigger
language plpgsql
as $$
begin
  -- service role / owner path: unrestricted
  if auth.uid() is null then
    return NEW;
  end if;
  -- firm member path: unrestricted (RLS already limited the row to their firm)
  if public.is_firm_member(OLD.firm_id) then
    return NEW;
  end if;
  -- household path: only status and evidence may change
  if (NEW.firm_id, NEW.matter_id, NEW.asset_id, NEW.task_type, NEW.title, NEW.due_date)
     is distinct from
     (OLD.firm_id, OLD.matter_id, OLD.asset_id, OLD.task_type, OLD.title, OLD.due_date) then
    raise exception 'household members may only update status and evidence on funding tasks';
  end if;
  return NEW;
end;
$$;

create trigger trg_funding_tasks_household_update_guard
before update on funding_tasks
for each row execute function public.guard_funding_task_update();

-- ---------------------------------------------------------------------------
-- 3. Indexes (FK lookups + RLS/policy subquery paths)
-- ---------------------------------------------------------------------------
create index idx_file_uploads_firm_id      on file_uploads (firm_id);
create index idx_file_uploads_household_id on file_uploads (household_id);
create index idx_file_uploads_matter_id    on file_uploads (matter_id);
create index idx_document_templates_firm_id     on document_templates (firm_id);
create index idx_document_packages_firm_id      on document_packages (firm_id);
create index idx_document_packages_matter_id    on document_packages (matter_id);
create index idx_document_packages_template_id  on document_packages (template_id);
create index idx_document_versions_package_id   on document_versions (package_id);
create index idx_document_versions_file_upload  on document_versions (file_upload_id);
create index idx_signing_checklists_firm_id     on signing_checklists (firm_id);
create index idx_signing_checklists_matter_id   on signing_checklists (matter_id);
create index idx_funding_tasks_firm_id          on funding_tasks (firm_id);
create index idx_funding_tasks_matter_id        on funding_tasks (matter_id);
create index idx_funding_tasks_asset_id         on funding_tasks (asset_id);
create index idx_funding_tasks_evidence_file    on funding_tasks (evidence_file_id);
create index idx_vault_shares_firm_id           on vault_shares (firm_id);
create index idx_vault_shares_matter_id         on vault_shares (matter_id);
create index idx_ai_interactions_firm_id        on ai_interactions (firm_id);
create index idx_ai_interactions_matter_id      on ai_interactions (matter_id);
create index idx_reminders_firm_id               on reminders (firm_id);
create index idx_reminders_matter_id             on reminders (matter_id);
create index idx_reminders_scheduled_for         on reminders (scheduled_for);

-- ---------------------------------------------------------------------------
-- 4. Row Level Security
-- Defense in depth: the API connects with a privileged role that bypasses
-- RLS and enforces app/core/authz.py server-side. Policies below reuse the
-- Slice 1 security-definer helpers so household <-> household_members joins
-- never recurse.
-- ---------------------------------------------------------------------------
alter table file_uploads       enable row level security;
alter table document_templates enable row level security;
alter table document_packages  enable row level security;
alter table document_versions  enable row level security;
alter table signing_checklists enable row level security;
alter table funding_tasks      enable row level security;
alter table vault_shares       enable row level security;
alter table ai_interactions    enable row level security;
alter table reminders          enable row level security;

-- ---------------------------------------------------------------------------
-- 4a. file_uploads — firm members get full access; household members may
--     SELECT and INSERT uploads for their own household only.
-- ---------------------------------------------------------------------------
create policy "file_uploads all by firm members"
on file_uploads for all
using (public.is_firm_member(file_uploads.firm_id))
with check (public.is_firm_member(file_uploads.firm_id));

create policy "file_uploads select by household members"
on file_uploads for select
using (public.is_household_member(file_uploads.household_id));

create policy "file_uploads insert by household members"
on file_uploads for insert
with check (public.is_household_member(file_uploads.household_id));

-- ---------------------------------------------------------------------------
-- 4b. document_templates — firm_admin full access; all firm staff SELECT.
--     Clients never see templates.
-- ---------------------------------------------------------------------------
create policy "document_templates select for firm members"
on document_templates for select
using (public.is_firm_member(document_templates.firm_id));

create policy "document_templates insert by firm admins"
on document_templates for insert
with check (public.is_firm_admin(document_templates.firm_id));

create policy "document_templates update by firm admins"
on document_templates for update
using (public.is_firm_admin(document_templates.firm_id))
with check (public.is_firm_admin(document_templates.firm_id));

create policy "document_templates delete by firm admins"
on document_templates for delete
using (public.is_firm_admin(document_templates.firm_id));

-- ---------------------------------------------------------------------------
-- 4c. document_packages — firm full access; household members SELECT only.
--     The household path is resolved through the parent matter.
-- ---------------------------------------------------------------------------
create policy "document_packages select for firm or household members"
on document_packages for select
using (
  public.is_firm_member(document_packages.firm_id)
  or exists (
    select 1 from matters m
    where m.id = document_packages.matter_id
      and public.is_household_member(m.household_id)
  )
);

create policy "document_packages insert by firm members"
on document_packages for insert
with check (public.is_firm_member(document_packages.firm_id));

create policy "document_packages update by firm members"
on document_packages for update
using (public.is_firm_member(document_packages.firm_id))
with check (public.is_firm_member(document_packages.firm_id));

create policy "document_packages delete by firm members"
on document_packages for delete
using (public.is_firm_member(document_packages.firm_id));

-- ---------------------------------------------------------------------------
-- 4d. document_versions — immutable. Firm members may SELECT and INSERT;
--     household members may SELECT only, resolved through the package's
--     matter. No UPDATE/DELETE policies (deny by default).
-- ---------------------------------------------------------------------------
create policy "document_versions select for firm or household members"
on document_versions for select
using (
  exists (
    select 1 from document_packages p
    where p.id = document_versions.package_id
      and public.is_firm_member(p.firm_id)
  )
  or exists (
    select 1 from document_packages p
    join matters m on m.id = p.matter_id
    where p.id = document_versions.package_id
      and public.is_household_member(m.household_id)
  )
);

create policy "document_versions insert by firm members"
on document_versions for insert
with check (
  exists (
    select 1 from document_packages p
    where p.id = document_versions.package_id
      and public.is_firm_member(p.firm_id)
  )
);

-- ---------------------------------------------------------------------------
-- 4e. signing_checklists — firm full access; household members may SELECT
--     and UPDATE the completion fields (the guard trigger in §2 limits the
--     columns). The household path is resolved through the parent matter.
-- ---------------------------------------------------------------------------
create policy "signing_checklists select for firm or household members"
on signing_checklists for select
using (
  exists (
    select 1 from matters m
    where m.id = signing_checklists.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "signing_checklists insert by firm members"
on signing_checklists for insert
with check (public.is_firm_member(signing_checklists.firm_id));

create policy "signing_checklists delete by firm members"
on signing_checklists for delete
using (public.is_firm_member(signing_checklists.firm_id));

create policy "signing_checklists update for firm or household members"
on signing_checklists for update
using (
  exists (
    select 1 from matters m
    where m.id = signing_checklists.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = signing_checklists.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

-- ---------------------------------------------------------------------------
-- 4f. funding_tasks — firm full access; household members may SELECT and
--     UPDATE status/evidence (the guard trigger in §2 limits the columns).
--     The household path is resolved through the parent matter.
-- ---------------------------------------------------------------------------
create policy "funding_tasks select for firm or household members"
on funding_tasks for select
using (
  exists (
    select 1 from matters m
    where m.id = funding_tasks.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

create policy "funding_tasks insert by firm members"
on funding_tasks for insert
with check (public.is_firm_member(funding_tasks.firm_id));

create policy "funding_tasks delete by firm members"
on funding_tasks for delete
using (public.is_firm_member(funding_tasks.firm_id));

create policy "funding_tasks update for firm or household members"
on funding_tasks for update
using (
  exists (
    select 1 from matters m
    where m.id = funding_tasks.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
)
with check (
  exists (
    select 1 from matters m
    where m.id = funding_tasks.matter_id
      and (public.is_firm_member(m.firm_id) or public.is_household_member(m.household_id))
  )
);

-- ---------------------------------------------------------------------------
-- 4g. vault_shares — firm full access; household members SELECT only on
--     their matters. Anonymous bearer access is handled by the API
--     (token check + expiry), never by RLS.
-- ---------------------------------------------------------------------------
create policy "vault_shares select for firm or household members"
on vault_shares for select
using (
  public.is_firm_member(vault_shares.firm_id)
  or exists (
    select 1 from matters m
    where m.id = vault_shares.matter_id
      and public.is_household_member(m.household_id)
  )
);

create policy "vault_shares insert by firm members"
on vault_shares for insert
with check (public.is_firm_member(vault_shares.firm_id));

create policy "vault_shares update by firm members"
on vault_shares for update
using (public.is_firm_member(vault_shares.firm_id))
with check (public.is_firm_member(vault_shares.firm_id));

create policy "vault_shares delete by firm members"
on vault_shares for delete
using (public.is_firm_member(vault_shares.firm_id));

-- ---------------------------------------------------------------------------
-- 4h. ai_interactions / reminders — firm full access; household members may
--     SELECT only the rows tied to their own matters (matter_id must be set).
-- ---------------------------------------------------------------------------
create policy "ai_interactions select for firm or household members"
on ai_interactions for select
using (
  public.is_firm_member(ai_interactions.firm_id)
  or (
    ai_interactions.matter_id is not null
    and exists (
      select 1 from matters m
      where m.id = ai_interactions.matter_id
        and public.is_household_member(m.household_id)
    )
  )
);

create policy "ai_interactions insert by firm members"
on ai_interactions for insert
with check (public.is_firm_member(ai_interactions.firm_id));

create policy "ai_interactions update by firm members"
on ai_interactions for update
using (public.is_firm_member(ai_interactions.firm_id))
with check (public.is_firm_member(ai_interactions.firm_id));

create policy "ai_interactions delete by firm members"
on ai_interactions for delete
using (public.is_firm_member(ai_interactions.firm_id));

create policy "reminders select for firm or household members"
on reminders for select
using (
  public.is_firm_member(reminders.firm_id)
  or (
    reminders.matter_id is not null
    and exists (
      select 1 from matters m
      where m.id = reminders.matter_id
        and public.is_household_member(m.household_id)
    )
  )
);

create policy "reminders insert by firm members"
on reminders for insert
with check (public.is_firm_member(reminders.firm_id));

create policy "reminders update by firm members"
on reminders for update
using (public.is_firm_member(reminders.firm_id))
with check (public.is_firm_member(reminders.firm_id));

create policy "reminders delete by firm members"
on reminders for delete
using (public.is_firm_member(reminders.firm_id));

-- ---------------------------------------------------------------------------
-- 5. updated_at triggers (tables that carry the column)
-- ---------------------------------------------------------------------------
create trigger trg_file_uploads_set_updated_at
before update on file_uploads
for each row execute function public.set_updated_at();

create trigger trg_document_templates_set_updated_at
before update on document_templates
for each row execute function public.set_updated_at();

create trigger trg_document_packages_set_updated_at
before update on document_packages
for each row execute function public.set_updated_at();

create trigger trg_signing_checklists_set_updated_at
before update on signing_checklists
for each row execute function public.set_updated_at();

create trigger trg_funding_tasks_set_updated_at
before update on funding_tasks
for each row execute function public.set_updated_at();
