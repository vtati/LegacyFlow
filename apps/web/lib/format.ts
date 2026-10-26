/** Shared display formatting helpers for the web UI. */

const INVALID_DATE = '—';

/** Turn a snake_case status into human-readable title case. */
export function formatStatus(status: string): string {
  return status
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

/** Format an ISO date string like "Sep 26, 2026"; '—' when missing or invalid. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return INVALID_DATE;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return INVALID_DATE;
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

/** Format an ISO timestamp with date and time; '—' when missing or invalid. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return INVALID_DATE;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return INVALID_DATE;
  return date.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/** Format an integer cent value as currency; '—' when unknown. */
export function formatMoney(valueCents: number | null | undefined, currency = 'USD'): string {
  if (valueCents == null) return INVALID_DATE;
  return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(valueCents / 100);
}

/**
 * Badge color classes for a status value. Same mapping the family dashboard
 * uses, shared so firm and family pages stay consistent.
 *
 * Warm-neutral pills; state is carried by a tinted dot (rendered via a
 * `before:` pseudo-element, never color-only), so the pill body stays calm:
 * sage = complete, terracotta tint = in progress, warm gray = not started,
 * red tint = attention.
 */
/** Shared status-dot treatment (colored dot before the label via ::before). */
export const STATUS_DOT_CLASS =
  'before:mr-1.5 before:inline-block before:size-1.5 before:rounded-full before:align-middle before:content-[""]';

export function statusBadgeClass(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized.includes('approv') || normalized.includes('complet')) {
    return `${STATUS_DOT_CLASS} bg-lf-success-100 text-lf-success-700 before:bg-lf-success-600`;
  }
  if (
    normalized.includes('review') ||
    normalized.includes('submit') ||
    normalized.includes('pending')
  ) {
    return `${STATUS_DOT_CLASS} bg-lf-accent-100 text-lf-accent-800 before:bg-lf-accent-600`;
  }
  if (normalized.includes('reject') || normalized.includes('block')) {
    return `${STATUS_DOT_CLASS} bg-lf-error-100 text-lf-error-700 before:bg-lf-error-600`;
  }
  return `${STATUS_DOT_CLASS} bg-lf-canvas-subtle text-lf-text-muted before:bg-lf-text-subtle`;
}
