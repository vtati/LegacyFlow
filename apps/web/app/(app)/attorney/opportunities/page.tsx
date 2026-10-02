'use client';

import { useCallback, useEffect, useState } from 'react';
import { HandHelping, Inbox } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, payoutApi, type MatterSummary } from '@/lib/api-client';
import { formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Attorney opportunities — unassigned matters the attorney can request.
 * Requests go to the firm admin; assignment stays manual.
 */
export default function AttorneyOpportunitiesPage() {
  const [matters, setMatters] = useState<MatterSummary[] | null>(null);
  const [requested, setRequested] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await firmApi.listMatters({ pageSize: 100 });
      // Unassigned matters in reviewable states.
      setMatters(
        res.items.filter(
          (m) =>
            !m.assigned_attorney_id &&
            ['INTAKE_SUBMITTED', 'ATTORNEY_REVIEW', 'CLARIFICATION_REQUESTED'].includes(
              m.status,
            ),
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load opportunities.');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const requestCase = useCallback(async (matterId: string) => {
    setActingId(matterId);
    setError(null);
    try {
      await payoutApi.requestCase(matterId);
      setRequested((prev) => new Set(prev).add(matterId));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to request this case.');
    } finally {
      setActingId(null);
    }
  }, []);

  if (error && matters === null) return <ErrorState error={new Error(error)} onRetry={() => void load()} />;
  if (matters === null) return <LoadingState label="Loading opportunities…" />;

  return (
    <div className="space-y-6 lf-animate-fade-up">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Opportunities</h1>
        <p className="text-sm text-lf-text-subtle">
          Unassigned matters you can request. The firm admin reviews requests and assigns
          cases manually — requesting does not auto-claim a case.
        </p>
      </div>

      {error && (
        <p role="alert" className="rounded-lf-card bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Inbox className="h-5 w-5 text-lf-primary" /> Available cases
          </CardTitle>
          <CardDescription>
            Client names are hidden until a case is assigned to you.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {matters.length === 0 ? (
            <EmptyState
              title="No open opportunities"
              description="There are no unassigned matters right now. Check back later."
            />
          ) : (
            <ul className="divide-y divide-lf-border">
              {matters.map((m) => {
                const wasRequested = requested.has(m.id);
                return (
                  <li key={m.id} className="flex flex-wrap items-center justify-between gap-3 py-4 first:pt-0 last:pb-0">
                    <div className="min-w-0">
                      <p className="font-medium text-lf-text">
                        {m.jurisdiction_code} · {formatStatus(m.status)}
                      </p>
                      <p className="text-xs text-lf-text-subtle">
                        Plan type and complexity shown after assignment. Fee:{' '}
                        {m.jurisdiction_code ? '$150 standard / $300 complex' : '$150'}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <span
                        className={cn(
                          'rounded-full px-2.5 py-1 text-xs font-medium',
                          statusBadgeClass(m.status),
                        )}
                      >
                        {formatStatus(m.status)}
                      </span>
                      <Button
                        size="sm"
                        variant={wasRequested ? 'outline' : 'default'}
                        disabled={wasRequested || actingId === m.id}
                        onClick={() => void requestCase(m.id)}
                        className="lf-pressable"
                      >
                        <HandHelping className="mr-1.5 h-4 w-4" />
                        {wasRequested
                          ? 'Requested'
                          : actingId === m.id
                            ? 'Requesting…'
                            : 'Request case'}
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
