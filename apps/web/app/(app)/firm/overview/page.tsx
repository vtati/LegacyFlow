'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type MatterSummary } from '@/lib/api-client';
import { fetchAllMatters, normalizeStatus } from '@/lib/firm-matters';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

const REVIEW_STATUS = 'ATTORNEY_REVIEW';
const CLARIFICATION_STATUS = 'CLARIFICATION_REQUESTED';
const ESCALATED_STATUS = 'ESCALATED';

const CLOSED_ESCALATION_STATUSES = new Set(['resolved', 'closed', 'dismissed', 'voided']);

interface AttentionMatter {
  matter: MatterSummary;
  reasons: string[];
}

function statusCards(matters: MatterSummary[]): { label: string; count: number }[] {
  const count = (statuses: string[]): number =>
    matters.filter((m) => statuses.includes(normalizeStatus(m.status))).length;
  return [
    { label: 'Total matters', count: matters.length },
    { label: 'Awaiting review', count: count([REVIEW_STATUS, CLARIFICATION_STATUS, ESCALATED_STATUS]) },
    { label: 'In intake', count: count(['DRAFT', 'INTAKE_IN_PROGRESS', 'INTAKE_SUBMITTED']) },
    { label: 'In signing & funding', count: count(['SIGNING_IN_PROGRESS', 'FUNDING_IN_PROGRESS']) },
    {
      label: 'Documents released',
      count: count(['APPROVED_FOR_ASSEMBLY', 'DOCUMENTS_RELEASED']),
    },
    { label: 'Implemented', count: count(['PLAN_IMPLEMENTED', 'ARCHIVED']) },
  ];
}

function AttentionRow({ matter, reasons }: AttentionMatter) {
  return (
    <li className="border-t border-lf-border first:border-t-0">
      <Link
        href={`/firm/matters/${matter.id}`}
        className="flex min-h-[44px] flex-col gap-1 px-4 py-3 hover:bg-lf-canvas-subtle sm:flex-row sm:items-center sm:justify-between"
      >
        <span>
          <span className="block font-medium text-lf-primary-700 underline-offset-4 hover:underline">
            {matter.title}
          </span>
          <span className="block text-sm text-lf-text-muted">{matter.household.name}</span>
        </span>
        <span className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
              statusBadgeClass(matter.status),
            )}
          >
            {formatStatus(matter.status)}
          </span>
          {reasons.map((reason) => (
            <span
              key={reason}
              className="inline-block rounded-full bg-lf-error-100 px-2.5 py-1 text-xs font-medium text-lf-error-700"
            >
              {reason}
            </span>
          ))}
        </span>
      </Link>
    </li>
  );
}

/** Firm overview dashboard: status counts, matters needing attention, recent activity. */
export default function FirmOverviewPage(): React.JSX.Element {
  const [matters, setMatters] = useState<MatterSummary[]>([]);
  const [attention, setAttention] = useState<AttentionMatter[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingAttention, setLoadingAttention] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchAllMatters()
      .then(setMatters)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (matters.length === 0) {
      setAttention([]);
      return;
    }
    setLoadingAttention(true);
    const candidates = matters.filter((m) => {
      const status = normalizeStatus(m.status);
      return status === REVIEW_STATUS || status === CLARIFICATION_STATUS || status === ESCALATED_STATUS;
    });
    Promise.all(
      candidates.map((matter) =>
        firmApi
          .listEscalations(matter.id)
          .then((escalations) => {
            const open = escalations.filter(
              (e) => !CLOSED_ESCALATION_STATUSES.has(e.status.toLowerCase()),
            );
            const reasons: string[] = [];
            const status = normalizeStatus(matter.status);
            if (status === ESCALATED_STATUS) reasons.push('Escalated');
            if (status === CLARIFICATION_STATUS) reasons.push('Clarification requested');
            if (open.length > 0) reasons.push(`${open.length} open escalation${open.length === 1 ? '' : 's'}`);
            return { matter, reasons };
          })
          .catch(() => ({ matter, reasons: ['Escalation check failed'] })),
      ),
    )
      .then(setAttention)
      .finally(() => setLoadingAttention(false));
  }, [matters]);

  const recent = [...matters]
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    .slice(0, 5);

  if (loading) return <LoadingState lines={6} label="Loading firm overview…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;

  const cards = statusCards(matters);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Firm overview</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Pipeline health, matters that need attention, and recent activity.
        </p>
      </div>

      <section aria-label="Matter counts by status" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((card) => (
          <Card key={card.label}>
            <CardContent className="p-5">
              <p className="text-3xl font-bold text-lf-text">{card.count}</p>
              <p className="mt-1 text-sm text-lf-text-muted">{card.label}</p>
            </CardContent>
          </Card>
        ))}
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Needs attention</CardTitle>
          <CardDescription>Matters awaiting review, clarification, or with open escalations.</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {loadingAttention ? (
            <div className="p-5">
              <LoadingState lines={3} label="Checking escalations…" />
            </div>
          ) : attention.length === 0 ? (
            <div className="p-5">
              <EmptyState
                title="Nothing needs attention"
                description="No matters are waiting on attorney review or carrying open escalations."
              />
            </div>
          ) : (
            <ul>{attention.map((a) => <AttentionRow key={a.matter.id} matter={a.matter} reasons={a.reasons} />)}</ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent matters</CardTitle>
          <CardDescription>Most recently updated.</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {recent.length === 0 ? (
            <div className="p-5">
              <EmptyState
                title="No matters yet"
                description="Matters appear here once the firm starts a plan for a household."
              />
            </div>
          ) : (
            <ul>
              {recent.map((matter) => (
                <li key={matter.id} className="border-t border-lf-border first:border-t-0">
                  <Link
                    href={`/firm/matters/${matter.id}`}
                    className="flex min-h-[44px] items-center justify-between gap-3 px-4 py-3 hover:bg-lf-canvas-subtle"
                  >
                    <span>
                      <span className="block font-medium text-lf-primary-700 underline-offset-4 hover:underline">
                        {matter.title}
                      </span>
                      <span className="block text-sm text-lf-text-muted">{matter.household.name}</span>
                    </span>
                    <span className="shrink-0 text-sm text-lf-text-muted">{formatDate(matter.updated_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
