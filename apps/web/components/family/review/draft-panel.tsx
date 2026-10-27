'use client';

import { CircleAlert, FileCheck2, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { DraftDocumentState, DraftSetState } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';

const TYPE_LABELS: Record<string, string> = {
  will: 'Last Will and Testament',
  poa: 'Power of Attorney',
  advance_directive: 'Advance Medical Directive',
  guardianship: 'Guardianship Designation',
  trust: 'Revocable Living Trust',
  codicil: 'Codicil',
  trust_amendment: 'Trust Amendment',
  instruction_letter: 'Instruction Letter',
};

/** Plain-language reason a document type is out of the generated set. */
const EXCLUDED_REASONS: Record<string, string> = {
  'wants_poa_document == false': 'You chose not to create a power of attorney.',
  'wants_healthcare_directive == false':
    'You chose not to create an advance medical directive.',
  'has_minor_children != true': 'Only needed when you have minor children.',
  'wants_revocable_trust != true': 'Only included when you choose a revocable living trust.',
};

const BLOCKER_TEXT: Record<string, string> = {
  not_complete: 'Finish all required intake answers before submitting.',
  drafts_missing: 'Create your draft documents first.',
  drafts_stale:
    'Your answers changed since the drafts were created — regenerate them so your attorney reviews exactly what you confirmed.',
  already_submitted: 'This plan has already been submitted to your attorney.',
  invalid_status: "Submission isn't available for this plan's current status.",
};

// Mirrors the API's post-submit set exactly. ESCALATED is deliberately
// NOT here: an escalation can route a matter out of intake before any
// submission, so it reads as invalid_status, not "submitted".
const POST_SUBMIT_STATUSES = new Set([
  'ATTORNEY_REVIEW',
  'CLARIFICATION_REQUESTED',
  'APPROVED_FOR_ASSEMBLY',
  'DOCUMENTS_RELEASED',
  'SIGNING_IN_PROGRESS',
  'FUNDING_IN_PROGRESS',
  'PLAN_IMPLEMENTED',
]);

function documentLabel(doc: DraftDocumentState): string {
  return doc.template_name ?? TYPE_LABELS[doc.template_type] ?? doc.template_type;
}

function documentStatus(doc: DraftDocumentState): string {
  if (!doc.included) {
    return EXCLUDED_REASONS[doc.signal] ?? 'Not part of your plan.';
  }
  if (!doc.generated) return 'Not created yet';
  if (doc.stale) return 'Out of date — regenerate before submitting';
  return doc.latest_version_at
    ? `Created ${formatDateTime(doc.latest_version_at)}`
    : 'Created';
}

interface DraftPanelProps {
  state: DraftSetState;
  generating: boolean;
  submitting: boolean;
  actionError: string | null;
  onGenerate: () => void;
  onSubmit: () => void;
}

/**
 * The draft panel on the family review page: the document set the
 * matter's pack produces, generate/regenerate, staleness, and submit to
 * the attorney — driven entirely by the state endpoint's can_* flags.
 */
export function DraftPanel({
  state,
  generating,
  submitting,
  actionError,
  onGenerate,
  onSubmit,
}: DraftPanelProps): React.JSX.Element {
  if (!state.enabled) {
    return (
      <section
        aria-label="Draft documents"
        className="rounded-lf-card bg-lf-panel-amber p-4 sm:p-5"
      >
        <h3 className="text-sm font-semibold text-lf-text">Draft documents</h3>
        <p className="mt-2 text-sm text-lf-text-muted">
          Self-serve draft creation isn&apos;t available for your firm yet — your firm
          will prepare your documents for you. You can still review and correct your
          answers above.
        </p>
      </section>
    );
  }

  const submitted =
    state.latest_packet != null || POST_SUBMIT_STATUSES.has(state.matter_status);
  const anyGenerated = state.documents.some((doc) => doc.included && doc.generated);
  const anyStale = state.documents.some((doc) => doc.included && doc.stale);

  return (
    <section
      aria-label="Draft documents"
      className="rounded-lf-card bg-lf-panel-violet p-4 sm:p-5"
    >
      <h3 className="text-sm font-semibold text-lf-text">Draft documents</h3>
      <p className="mt-1 text-sm text-lf-text-muted">
        These drafts are watermarked and not legally valid until your attorney approves
        and releases the final documents.
      </p>

      <ul className="mt-3 divide-y divide-lf-border/60">
        {state.documents.map((doc) => (
          <li key={doc.template_type} className="flex items-start justify-between gap-3 py-2.5">
            <div>
              <p className="text-sm font-medium text-lf-text">{documentLabel(doc)}</p>
              <p className="text-xs text-lf-text-subtle">{documentStatus(doc)}</p>
            </div>
            {doc.included && doc.generated && !doc.stale && (
              <FileCheck2 className="mt-0.5 size-4 shrink-0 text-lf-success-600" aria-hidden="true" />
            )}
          </li>
        ))}
      </ul>

      {submitted ? (
        <div
          role="status"
          className="mt-4 rounded-lf-control border border-lf-success-600 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
        >
          <p className="font-medium">Submitted for attorney review</p>
          <p className="mt-1">
            {state.assigned_attorney
              ? `Your attorney is ${state.assigned_attorney.full_name}. `
              : 'Your attorney has your plan. '}
            {state.latest_packet
              ? `Review packet version ${state.latest_packet.version}. `
              : ''}
            Your drafts are frozen as submitted — if anything needs to change, your
            attorney will ask.
          </p>
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {anyStale && (
            <p
              role="alert"
              className="flex items-start gap-2 rounded-lf-control border border-lf-warning-500 bg-lf-warning-50 px-4 py-3 text-sm text-lf-warning-700"
            >
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              Your answers changed after these drafts were created. Regenerate them so
              your attorney reviews exactly what you confirmed.
            </p>
          )}
          {!state.applicable.complete && (
            <p className="text-sm text-lf-text-muted">
              Draft creation unlocks once every required answer is complete.
            </p>
          )}
          {state.submit_blockers.length > 0 && state.applicable.complete && (
            <ul className="space-y-1 text-sm text-lf-text-muted">
              {state.submit_blockers.map((blocker) => (
                <li key={blocker}>{BLOCKER_TEXT[blocker] ?? blocker}</li>
              ))}
            </ul>
          )}
          {actionError && (
            <p role="alert" className="text-sm text-lf-error-700">
              {actionError}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="button" onClick={onGenerate} disabled={!state.can_generate || generating || submitting}>
              {generating
                ? 'Creating drafts…'
                : anyGenerated
                  ? 'Regenerate drafts'
                  : 'Create draft documents'}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={onSubmit}
              disabled={!state.can_submit || generating || submitting}
            >
              <Send className="size-4" aria-hidden="true" />
              {submitting ? 'Submitting…' : 'Submit to attorney'}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
