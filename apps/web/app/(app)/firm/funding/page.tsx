'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type FundingTask, type MatterSummary } from '@/lib/api-client';
import { fetchAllMatters } from '@/lib/firm-matters';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

type StatusFilter = 'all' | 'pending' | 'in_progress' | 'complete';

const FILTER_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'complete', label: 'Complete' },
];

interface FundingRow {
  matter: MatterSummary;
  tasks: FundingTask[];
  failed: boolean;
}

/** Firm funding: cross-matter funding tasks with a status filter. */
export default function FirmFundingPage(): React.JSX.Element {
  const [rows, setRows] = useState<FundingRow[]>([]);
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchAllMatters()
      .then((matters) =>
        Promise.all(
          matters.map((matter) =>
            firmApi
              .listFundingTasks(matter.id)
              .then((tasks) => ({ matter, tasks, failed: false }) as FundingRow)
              .catch(() => ({ matter, tasks: [], failed: true }) as FundingRow),
          ),
        ),
      )
      .then(setRows)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = useMemo(
    () =>
      rows
        .map((row) => ({
          ...row,
          tasks: row.tasks.filter((task) => filter === 'all' || task.status === filter),
        }))
        .filter((row) => row.tasks.length > 0 || row.failed),
    [rows, filter],
  );

  if (loading) return <LoadingState lines={6} label="Loading funding tasks…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;

  const counts = {
    pending: rows.flatMap((r) => r.tasks).filter((t) => t.status === 'pending').length,
    in_progress: rows.flatMap((r) => r.tasks).filter((t) => t.status === 'in_progress').length,
    complete: rows.flatMap((r) => r.tasks).filter((t) => t.status === 'complete').length,
  };
  const failedCount = rows.filter((r) => r.failed).length;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Funding</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Funding tasks across every matter — filter by status and open the matter for evidence
          and escalations.
        </p>
      </div>

      <section aria-label="Funding summary" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { label: 'Matters tracked', count: rows.length },
          { label: 'Pending tasks', count: counts.pending },
          { label: 'In progress', count: counts.in_progress },
          { label: 'Complete', count: counts.complete },
        ].map((card) => (
          <Card key={card.label}>
            <CardContent className="p-5">
              <p className="text-3xl font-bold text-lf-text">{card.count}</p>
              <p className="mt-1 text-sm text-lf-text-muted">{card.label}</p>
            </CardContent>
          </Card>
        ))}
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <Label id="funding-filter-label">Status</Label>
        <div role="group" aria-labelledby="funding-filter-label" className="flex flex-wrap gap-2">
          {FILTER_OPTIONS.map((option) => (
            <Button
              key={option.value}
              type="button"
              variant={filter === option.value ? 'default' : 'outline'}
              size="sm"
              className="min-h-[44px]"
              aria-pressed={filter === option.value}
              onClick={() => setFilter(option.value)}
            >
              {option.label}
            </Button>
          ))}
        </div>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          title="No funding tasks"
          description={
            filter === 'all'
              ? 'Funding tasks appear here once they are generated for a matter.'
              : 'No funding tasks match this status filter.'
          }
        />
      ) : (
        <div className="space-y-4">
          {failedCount > 0 && (
            <p role="note" className="text-sm text-lf-text-muted">
              {failedCount} {failedCount === 1 ? 'matter' : 'matters'} skipped: funding tasks could
              not be loaded for them.
            </p>
          )}
          {visible.map((row) => (
            <Card key={row.matter.id}>
              <CardHeader>
                <CardTitle className="text-base">
                  <Link
                    href={`/firm/matters/${row.matter.id}`}
                    className="text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
                  >
                    {row.matter.title}
                  </Link>
                </CardTitle>
              </CardHeader>
              <CardContent>
                {row.failed ? (
                  <p className="text-sm text-lf-text-muted">
                    Funding tasks could not be loaded for this matter.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {row.tasks.map((task) => (
                      <li
                        key={task.id}
                        className="flex items-start justify-between gap-3 rounded-lf-control border border-lf-border px-3 py-2.5"
                      >
                        <span>
                          <span className="block text-sm font-medium text-lf-text">
                            {task.title}
                          </span>
                          {task.description && (
                            <span className="block text-sm text-lf-text-muted">
                              {task.description}
                            </span>
                          )}
                          <span className="block text-xs text-lf-text-subtle">
                            Updated {formatDate(task.updated_at)}
                          </span>
                        </span>
                        <span
                          className={cn(
                            'inline-block shrink-0 rounded-full px-2.5 py-1 text-xs font-medium',
                            statusBadgeClass(task.status),
                          )}
                        >
                          {formatStatus(task.status)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
