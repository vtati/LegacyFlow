'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type MatterSummary } from '@/lib/api-client';
import { fetchAllMatters, normalizeStatus } from '@/lib/firm-matters';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

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
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Review queue</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
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
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <caption className="sr-only">Matters awaiting review</caption>
              <thead>
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
              <tbody>
                {rows.map((row) => (
                  <tr key={row.matter.id} className="border-t border-lf-border">
                    <td className="px-4 py-3">
                      <Link
                        href={`/firm/matters/${row.matter.id}`}
                        className="font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
                      >
                        {row.matter.title}
                      </Link>
                      <p className="mt-0.5 text-sm text-lf-text-muted">{row.matter.household.name}</p>
                      {row.failed && (
                        <p className="mt-0.5 text-xs text-lf-error-700">
                          Some details could not be loaded for this matter.
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
                          statusBadgeClass(row.matter.status),
                        )}
                      >
                        {formatStatus(row.matter.status)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text">{row.pendingGates}</td>
                    <td className="px-4 py-3 text-sm text-lf-text">{row.openClarifications}</td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
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
