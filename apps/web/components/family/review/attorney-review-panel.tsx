'use client';

import { useState } from 'react';
import { CircleCheck, MessageCircleQuestion, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type {
  AttorneyReviewFinding,
  AttorneyReviewFindings,
} from '@/lib/api-client';

const DOC_LABELS: Record<string, string> = {
  will: 'Last Will and Testament',
  poa: 'Power of Attorney',
  advance_directive: 'Advance Medical Directive',
  guardianship: 'Guardianship Designation',
  trust: 'Revocable Living Trust',
  codicil: 'Codicil',
  trust_amendment: 'Trust Amendment',
  instruction_letter: 'Instruction Letter',
};

const SEVERITY_LABELS: Record<AttorneyReviewFinding['severity'], string> = {
  should_fix: 'Worth fixing',
  question_for_attorney: 'Question for your attorney',
};

interface AttorneyReviewPanelProps {
  data: AttorneyReviewFindings | null;
  variant?: 'family' | 'firm';
  onAcknowledge: (findingId: string, note: string) => void;
  onRun?: () => void;
  actionError?: string | null;
}

function groupByDocument(
  findings: AttorneyReviewFinding[],
): Array<{ document: string | null, items: AttorneyReviewFinding[] }> {
  const groups: Array<{ document: string | null, items: AttorneyReviewFinding[] }> = [];
  for (const finding of findings) {
    const key = finding.location.document;
    const group = groups.find((candidate) => candidate.document === key);
    if (group) group.items.push(finding);
    else groups.push({ document: key, items: [finding] });
  }
  return groups;
}

function FindingRow({
  finding,
  onAcknowledge,
}: {
  finding: AttorneyReviewFinding;
  onAcknowledge: (findingId: string, note: string) => void;
}): React.JSX.Element {
  const [note, setNote] = useState('');
  const acknowledged = finding.disposition === 'acknowledged';
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        {finding.severity === 'should_fix' ? (
          <TriangleAlert className="size-4 text-lf-warning-600" aria-hidden="true" />
        ) : (
          <MessageCircleQuestion className="size-4 text-lf-text-subtle" aria-hidden="true" />
        )}
        <span className="text-xs font-semibold uppercase tracking-wide text-lf-text-muted">
          {SEVERITY_LABELS[finding.severity]}
        </span>
        {acknowledged && (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-lf-success-700">
            <CircleCheck className="size-3.5" aria-hidden="true" />
            Acknowledged
          </span>
        )}
      </div>
      <p className="mt-1 text-sm text-lf-text">{finding.explanation}</p>
      {finding.location.excerpt && (
        <p className="mt-1 text-xs italic text-lf-text-subtle">
          &ldquo;{finding.location.excerpt}&rdquo;
        </p>
      )}
      {finding.suggested_fix && (
        <p className="mt-1 text-sm text-lf-text-muted">{finding.suggested_fix}</p>
      )}
      {acknowledged ? (
        <div className="mt-2 rounded-lf-control bg-lf-canvas-subtle px-3 py-2 text-sm text-lf-text-muted">
          <p>
            Acknowledged
            {finding.acknowledged_by_actor === 'firm'
              ? ' by the firm’s team'
              : finding.acknowledged_by_actor === 'family'
                ? ' by the family'
                : ''}
            {finding.acknowledged_at
              ? ` · ${new Date(finding.acknowledged_at).toLocaleDateString()}`
              : ''}
          </p>
          {finding.acknowledgment_note && <p>Note: {finding.acknowledgment_note}</p>}
        </div>
      ) : (
        <div className="mt-2">
          <div className="flex flex-wrap items-center gap-2">
            <input
              id={`ack-note-${finding.id}`}
              type="text"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Why is this OK as it is?"
              aria-label="Note for your attorney"
              aria-describedby={`ack-note-help-${finding.id}`}
              className="h-11 min-w-56 flex-1 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 text-sm text-lf-text sm:h-9"
            />
            <Button
              type="button"
              variant="outline"
              disabled={note.trim().length === 0}
              onClick={() => onAcknowledge(finding.id, note.trim())}
            >
              Acknowledge
            </Button>
          </div>
          {/* UI-022: persistent helper — a disabled button can't be
              focused, so without this the note requirement is
              undiscoverable for keyboard users. */}
          <p id={`ack-note-help-${finding.id}`} className="mt-1.5 text-xs text-lf-text-subtle">
            A short note is required so your attorney can see why this is OK
          </p>
        </div>
      )}
    </li>
  );
}

/**
 * Automated pre-review findings (Attorney Reviewer, design §9.6/§9.7).
 *
 * Advisory by construction: the panel explains, suggests, and records
 * acknowledgments — it never blocks anything, and its copy never
 * claims a legal review happened. Renders nothing when the firm flag
 * is off, so other firms' surfaces are untouched.
 */
export function AttorneyReviewPanel({
  data,
  variant = 'family',
  onAcknowledge,
  onRun,
  actionError,
}: AttorneyReviewPanelProps): React.JSX.Element | null {
  if (!data || !data.enabled) return null;

  // ERR-020: a run is degraded when the API flags it (Batch D) — older
  // payloads signal the same state via model_id. A degraded run must
  // never render identically to a full clean run, findings or not.
  const isDegraded =
    data.run != null &&
    (data.run.degraded === true || data.run.model_id === 'deterministic_only');

  return (
    <section
      aria-label="Automated pre-review"
      className="rounded-lf-card bg-lf-panel-violet p-4 sm:p-5"
    >
      <h3 className="text-sm font-semibold text-lf-text">Automated pre-review</h3>
      <p className="mt-1 text-sm text-lf-text-muted">
        {variant === 'firm'
          ? 'Advisory findings from the automated pre-review of the family\u2019s drafts. Acknowledging a finding records the override on the packet; the release gate is unchanged.'
          : 'An automated check of your drafts for inconsistencies \u2014 a name spelled two ways, a missing piece, numbers that don\u2019t add up. It isn\u2019t a legal review and it never blocks your submission; your attorney makes the final decisions.'}
      </p>

      {data.run && (
        <p className="mt-1 text-xs text-lf-text-subtle">
          Findings are for the matter’s current packet · pre-review ran{' '}
          {new Date(data.run.created_at).toLocaleString()}
        </p>
      )}

      {actionError && (
        <p role="alert" className="mt-3 text-sm text-lf-error-700">
          {actionError}
        </p>
      )}

      {/* ERR-025: a failed last attempt is not "never attempted" — say
          so and offer the retry instead of the no-run copy. */}
      {!data.run && data.last_failed_run && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <p className="text-sm text-lf-text-muted">
            The last pre-review attempt failed — try again.
            {variant === 'family'
              ? ' Your drafts are unaffected, and the pre-review also runs automatically when you submit.'
              : ''}
          </p>
          {onRun && (
            <Button type="button" variant="outline" onClick={onRun}>
              Run pre-review
            </Button>
          )}
        </div>
      )}

      {!data.run && !data.last_failed_run && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <p className="text-sm text-lf-text-muted">
            {variant === 'firm'
              ? 'No pre-review has run on the current drafts yet.'
              : 'The pre-review runs automatically when you create your drafts.'}
          </p>
          {onRun && (
            <Button type="button" variant="outline" onClick={onRun}>
              Run pre-review
            </Button>
          )}
        </div>
      )}

      {/* ERR-020: the degraded note shows whenever the current run is
          degraded — not only when findings are empty. */}
      {data.run && isDegraded && (
        <p role="status" className="mt-3 flex items-center gap-2 text-sm text-lf-text-muted">
          <CircleCheck className="size-4 shrink-0" aria-hidden="true" />
          {data.findings.length === 0
            ? variant === 'firm'
              ? 'No issues found by the automated checks. The judgment pass hasn’t run yet — deterministic checks only; it runs at submit, or you can run the full pre-review now.'
              : 'No issues found by the automated checks. The judgment pass hasn’t run yet — deterministic checks only. The judgment pass runs when you submit your drafts for attorney review.'
            : variant === 'firm'
              ? 'The judgment pass hasn’t run yet — deterministic checks only. The findings below are from the deterministic checks.'
              : 'The judgment pass hasn’t run yet — deterministic checks only. The findings below are from the automated checks; the judgment pass runs when you submit your drafts for attorney review.'}
        </p>
      )}

      {data.run && data.findings.length === 0 && !isDegraded && (
        <p role="status" className="mt-3 flex items-center gap-2 text-sm text-lf-success-700">
          <CircleCheck className="size-4" aria-hidden="true" />
          No issues found by the automated pre-review.
        </p>
      )}

      {groupByDocument(data.findings).map((group) => (
        <div key={group.document ?? 'general'} className="mt-2">
          <h4 className="mt-3 text-xs font-semibold uppercase tracking-wide text-lf-text-subtle">
            {group.document
              ? (DOC_LABELS[group.document] ?? group.document)
              : 'Across your documents'}
          </h4>
          <ul className="divide-y divide-lf-border/60">
            {group.items.map((finding) => (
              <FindingRow key={finding.id} finding={finding} onAcknowledge={onAcknowledge} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
