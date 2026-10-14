'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type MatterSummary } from '@/lib/api-client';
import { fetchAllMatters, normalizeStatus } from '@/lib/firm-matters';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Responsive table cells (presentation only): below the md breakpoint each
 * table row becomes a stacked label/value card. Labels come from the cell's
 * data-th attribute. Same table, same data, no behavior change.
 */
const MOBILE_TABLE_CELL =
  'max-md:block max-md:border-0 max-md:px-0 max-md:py-1.5 max-md:before:mb-1 max-md:before:block max-md:before:text-[11px] max-md:before:font-semibold max-md:before:uppercase max-md:before:tracking-wider max-md:before:text-lf-text-subtle max-md:before:content-[attr(data-th)]';

/** Matters that may need attorney eyes. Server statuses are UPPER_SNAKE_CASE. */
const QUEUE_STATUSES = new Set([
  'DRAFT',
  'INTAKE_IN_PROGRESS',
  'INTAKE_SUBMITTED',
  'ATTORNEY_REVIEW',
  'CLARIFICATION_REQUESTED',
  'ESCALATED',
]);

interface QueueRow {
  matter: MatterSummary;
  pendingGates: number;
  openClarifications: number;
  latestPacketVersion: number | null;
  latestPacketDate: string | null;
  failed: boolean;
}

async function loadRow(matter: MatterSummary): Promise<QueueRow> {
  const results = await Promise.allSettled([
    firmApi.listGates(matter.id),
    firmApi.listClarifications(matter.id),
    firmApi.listReviewPackets(matter.id),
  ]);
  const failed = results.some((r) => r.status === 'rejected');
  const gates = results[0].status === 'fulfilled' ? results[0].value : [];
  const clarifications = results[1].status === 'fulfilled' ? results[1].value : [];
  const packets = results[2].status === 'fulfilled' ? results[2].value : [];
  const latest = [...packets].sort((a, b) => b.version - a.version)[0] ?? null;
  return {
    matter,
    pendingGates: gates.filter((g) => g.status === 'pending').length,
    openClarifications: clarifications.filter((c) => c.status === 'open').length,
    latestPacketVersion: latest?.version ?? null,
    latestPacketDate: latest?.created_at ?? null,
    failed,
  };
}

/** Firm review queue: one row per active matter with its blockers. */
export default function FirmReviewQueuePage(): React.JSX.Element {
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchAllMatters()
      .then((matters) => {
        const queue = matters.filter((m) => QUEUE_STATUSES.has(normalizeStatus(m.status)));
        return Promise.all(queue.map(loadRow));
      })
      .then(setRows)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) return <LoadingState lines={6} label="Loading review queue…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Review queue</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Every matter that can use attorney attention, with its pending gates, open
          clarifications, and latest review packet.
        </p>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="Queue is clear"
          description="No matters are currently in review-related statuses."
        />
      ) : (
        <Card>
          <div className="overflow-x-auto md:max-h-[75vh] md:overflow-y-auto">
            <table className="w-full text-left text-sm tabular-nums md:min-w-[720px]">
              <caption className="sr-only">Matters awaiting review</caption>
              <thead className="max-md:hidden md:sticky md:top-0 md:bg-lf-canvas-elevated">
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Client / household
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Pending gates
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Open clarifications
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Latest packet
                  </th>
                </tr>
              </thead>
              <tbody className="max-md:block max-md:space-y-3 max-md:bg-lf-canvas-subtle max-md:p-4">
                {rows.map((row) => (
                  <tr key={row.matter.id} className="border-t border-lf-border transition-colors hover:bg-lf-canvas-subtle max-md:block max-md:rounded-lf-card max-md:border max-md:border-lf-border max-md:bg-lf-canvas-elevated max-md:p-4">
                    <td data-th="Client / household" className={`px-4 py-3 ${MOBILE_TABLE_CELL}`}>
                      <Link
                        href={`/firm/matters/${row.matter.id}`}
                        className="font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
                      >
                        {row.matter.title || 'Untitled matter'}
                      </Link>
                      <p className="mt-0.5 text-sm text-lf-text-muted">{row.matter.household?.name ?? 'Household'}</p>
                      {row.failed && (
                        <p className="mt-0.5 text-xs text-lf-error-700">
                          Some details could not be loaded for this matter.
                        </p>
                      )}
                    </td>
                    <td data-th="Status" className={`px-4 py-3 ${MOBILE_TABLE_CELL}`}>
                      <span
                        className={cn(
                          'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
                          statusBadgeClass(row.matter.status),
                        )}
                      >
                        {formatStatus(row.matter.status)}
                      </span>
                    </td>
                    <td data-th="Pending gates" className={`px-4 py-3 text-sm text-lf-text ${MOBILE_TABLE_CELL}`}>{row.pendingGates}</td>
                    <td data-th="Open clarifications" className={`px-4 py-3 text-sm text-lf-text ${MOBILE_TABLE_CELL}`}>{row.openClarifications}</td>
                    <td data-th="Latest packet" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {row.latestPacketVersion == null
                        ? 'No packet yet'
                        : `v${row.latestPacketVersion} · ${formatDate(row.latestPacketDate)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
