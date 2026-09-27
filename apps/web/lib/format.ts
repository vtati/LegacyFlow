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
 */
export function statusBadgeClass(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized.includes('approv') || normalized.includes('complet')) {
    return 'bg-lf-success-100 text-lf-success-700';
  }
  if (
    normalized.includes('review') ||
    normalized.includes('submit') ||
    normalized.includes('pending')
  ) {
    return 'bg-lf-accent-100 text-lf-accent-800';
  }
  if (normalized.includes('reject') || normalized.includes('block')) {
    return 'bg-lf-error-100 text-lf-error-700';
  }
  return 'bg-lf-primary-50 text-lf-primary-800';
}
