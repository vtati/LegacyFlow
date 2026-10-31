import { STATUS_DOT_CLASS, formatStatus } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Jewel status pill for family surfaces (Lumen Aurora Phase 3).
 *
 * Family-only by design: the shared `statusBadgeClass` in lib/format stays
 * untouched so professional surfaces render exactly as before. Tones map
 * exhaustively onto the API's MatterStatus enum (state_machine.py):
 * mint = confirmed milestones (approved for assembly, plan implemented),
 * sky = with the firm (submitted, under review, documents released),
 * amber = needs attention (in progress, clarification requested),
 * rose = blocked/escalated, neutral = draft/archived. State is never
 * color-only — the tinted dot rides along (the shared STATUS_DOT_CLASS
 * pattern from lib/format).
 */
function toneClass(status: string): string {
  const normalized = status.toLowerCase();
  if (
    normalized.includes('approv') ||
    normalized.includes('complet') ||
    normalized.includes('implement')
  ) {
    return `${STATUS_DOT_CLASS} bg-lf-success-100 text-lf-success-700 before:bg-lf-success-600`;
  }
  if (
    normalized.includes('reject') ||
    normalized.includes('block') ||
    normalized.includes('escalat')
  ) {
    return `${STATUS_DOT_CLASS} bg-lf-error-100 text-lf-error-700 before:bg-lf-error-600`;
  }
  if (normalized.includes('clarification')) {
    return `${STATUS_DOT_CLASS} bg-lf-warning-100 text-lf-warning-700 before:bg-lf-warning-500`;
  }
  if (
    normalized.includes('review') ||
    normalized.includes('submit') ||
    normalized.includes('pending') ||
    normalized.includes('releas')
  ) {
    return `${STATUS_DOT_CLASS} bg-lf-sky-100 text-lf-sky-800 before:bg-lf-sky-600`;
  }
  if (normalized.includes('progress') || normalized.includes('active')) {
    return `${STATUS_DOT_CLASS} bg-lf-warning-100 text-lf-warning-700 before:bg-lf-warning-500`;
  }
  return `${STATUS_DOT_CLASS} bg-lf-canvas-subtle text-lf-text-muted before:bg-lf-text-subtle`;
}

/**
 * Family-facing labels for statuses whose internal name is jargon.
 * ESCALATED is the state machine's word for "a blocking item routed
 * this plan to the attorney" — a family should never have to decode
 * it (2026-10-31: the owner, testing as a family user, read the pill
 * verbatim as "it says that it's escalated or something").
 */
const FAMILY_STATUS_LABELS: Record<string, string> = {
  ESCALATED: 'With your attorney',
};

export function familyStatusLabel(status: string): string {
  return FAMILY_STATUS_LABELS[status.toUpperCase()] ?? formatStatus(status);
}

export function StatusPill({
  status,
  className,
}: {
  status: string;
  className?: string;
}): React.JSX.Element {
  return (
    <span
      className={cn(
        'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
        toneClass(status),
        className,
      )}
    >
      {familyStatusLabel(status)}
    </span>
  );
}
