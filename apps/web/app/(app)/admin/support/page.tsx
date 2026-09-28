'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { EmptyState, LoadingState } from '@/components/family/page-state';
import { AdminErrorState, FirmScopeNote } from '@/components/admin/admin-page-state';
import { firmApi, type Escalation } from '@/lib/api-client';
import { formatDateTime, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

const MATTERS_PAGE_SIZE = 100;

/** Actual escalation statuses (backend ESCALATION_STATUSES). */
type StatusFilter = 'all' | 'open' | 'resolved' | 'dismissed';

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All statuses' },
  { value: 'open', label: 'Open' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'dismissed', label: 'Dismissed' },
];

interface EscalationWithMatter extends Escalation {
  matterTitle: string;
}

function EscalationRow({ escalation }: { escalation: EscalationWithMatter }) {
  return (
    <tr className="border-t border-lf-border">
      <td className="whitespace-nowrap px-4 py-3 text-sm text-lf-text-muted">
        {formatDateTime(escalation.created_at)}
      </td>
      <td className="px-4 py-3">
        <Link
          href={`/firm/matters/${escalation.matter_id}`}
          className="rounded-lf-control font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
        >
          {escalation.matterTitle || 'Untitled matter'}
        </Link>
      </td>
      <td className="px-4 py-3">
        <code className="rounded-lf-control bg-lf-canvas-subtle px-2 py-1 text-xs text-lf-text">
          {escalation.rule_id}
        </code>
      </td>
      <td className="px-4 py-3">
        <span
          className={cn(
            'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
            statusBadgeClass(escalation.severity),
          )}
        >
          {formatStatus(escalation.severity)}
        </span>
      </td>
      <td className="px-4 py-3">
        <span
          className={cn(
            'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
            statusBadgeClass(escalation.status),
          )}
        >
          {formatStatus(escalation.status)}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-lf-text-muted">{escalation.outcome}</td>
    </tr>
  );
}

/**
 * Admin → Support: household support requests, i.e. escalations recorded on
 * your firm's matters. Aggregated per matter; status is the escalation's own
 * lifecycle (open / resolved / dismissed), outcome is the rule outcome text.
 */
export default function AdminSupportPage(): React.JSX.Element {
  const [escalations, setEscalations] = useState<EscalationWithMatter[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listMatters({ page: 1, pageSize: MATTERS_PAGE_SIZE })
      .then((matters) =>
        Promise.all(
          matters.items.map((matter) =>
            firmApi
              .listEscalations(matter.id)
              .then((items) =>
                items.map((escalation) => ({
                  ...escalation,
                  matterTitle: matter.title,
                })),
              )
              .catch(() => [] as EscalationWithMatter[]),
          ),
        ),
      )
      .then((perMatter) => {
        const all = perMatter.flat();
        all.sort(
          (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        );
        setEscalations(all);
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered =
    statusFilter === 'all'
      ? escalations
      : escalations.filter((escalation) => escalation.status === statusFilter);
  const openCount = escalations.filter((e) => e.status === 'open').length;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Support</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Household support requests from your firm — every escalation rule that fired on a
          client matter.
        </p>
      </div>

      <FirmScopeNote>
        Only matters belonging to your firm are included. Open requests are the ones still
        awaiting attention.
      </FirmScopeNote>

      <div className="flex flex-wrap items-center justify-between gap-3">
        {!loading && !error && (
          <p className="text-sm text-lf-text-muted">
            {escalations.length}{' '}
            {escalations.length === 1 ? 'request' : 'requests'} · {openCount} open
          </p>
        )}
        <div className="flex items-center gap-2">
          <Label htmlFor="escalation-status">Status</Label>
          <select
            id="escalation-status"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
            className="min-h-[44px] rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading && <LoadingState lines={6} label="Loading support requests…" />}

      {error != null && !loading ? <AdminErrorState error={error} onRetry={load} /> : null}

      {!loading && !error && filtered.length === 0 && (
        <EmptyState
          title={escalations.length === 0 ? 'No support requests' : 'No requests match this status'}
          description={
            escalations.length === 0
              ? 'Escalations appear here when intake rules fire on a matter.'
              : 'Try a different status filter.'
          }
        />
      )}

      {!loading && !error && filtered.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-sm">
              <caption className="sr-only">Support requests (escalations)</caption>
              <thead>
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Raised
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Matter
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Rule
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Severity
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Outcome
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((escalation) => (
                  <EscalationRow key={escalation.id} escalation={escalation} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {!loading && !error && (
        <div className="flex justify-end">
          <Button type="button" variant="outline" className="min-h-[44px]" onClick={load}>
            Refresh
          </Button>
        </div>
      )}
    </div>
  );
}
