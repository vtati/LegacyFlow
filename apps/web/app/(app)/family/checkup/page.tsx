'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, CheckCircle2, CircleAlert } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { useMatters } from '@/components/family/matter-context';
import {
  ApiError,
  api,
  firmApi,
  type FundingTask,
  type Gate,
  type Clarification,
  type MatterSummary,
  type SigningChecklist,
} from '@/lib/api-client';
import { mapWithConcurrency } from '@/lib/concurrency';
import { formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

const NOMINATION_TYPES = ['guardian', 'executor', 'trustee'] as const;

const NOMINATION_LABELS: Record<string, string> = {
  guardian: 'guardian',
  executor: 'executor',
  trustee: 'trustee',
};

interface CheckupItem {
  label: string;
  detail: string | null;
  href: string;
}

interface MatterCheckup {
  matter: MatterSummary;
  items: CheckupItem[];
  /** True when some data sources failed and the list may be incomplete. */
  partial: boolean;
}

/** 404/403 on a checkup source means "not started yet" — skip it, don't fail the page. */
function isNotStarted(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 403);
}

async function buildMatterCheckup(matter: MatterSummary): Promise<MatterCheckup> {
  const items: CheckupItem[] = [];
  let partial = false;

  const settled = await Promise.allSettled([
    api.getIntakeProgress(matter.id),
    firmApi.listGates(matter.id),
    firmApi.listClarifications(matter.id),
    api.getChecklist(matter.id),
    api.listFundingTasks(matter.id),
    ...NOMINATION_TYPES.map((type) => api.listNominations(matter.id, type)),
  ]);
  const [progressResult, gatesResult, clarificationsResult, checklistResult, fundingResult] =
    settled;

  if (progressResult.status === 'fulfilled') {
    const overall = progressResult.value.overall;
    if (overall && overall.required_total > 0 && overall.required_answered < overall.required_total) {
      const remaining = overall.required_total - overall.required_answered;
      items.push({
        label: 'Finish intake',
        detail: `${remaining} required ${remaining === 1 ? 'answer' : 'answers'} still missing`,
        href: `/family/intake/${matter.id}`,
      });
    }
  } else if (!isNotStarted(progressResult.reason)) {
    partial = true;
  }

  if (gatesResult.status === 'fulfilled') {
    const pending: Gate[] = gatesResult.value.filter((gate) => gate.status === 'pending');
    for (const gate of pending) {
      items.push({
        label: `Approval pending: ${formatStatus(gate.gate_type)}`,
        detail: 'Waiting on your firm — no action from you, but worth knowing',
        href: '/family/documents',
      });
    }
  } else if (!isNotStarted(gatesResult.reason)) {
    partial = true;
  } else {
    // No gate visibility: fall back to the matter status as the review signal.
    const status = matter.status.toLowerCase();
    if (status.includes('review') || status.includes('submit')) {
      items.push({
        label: 'With your firm for review',
        detail: 'Your intake is being reviewed — watch for questions from your attorney',
        href: '/family/documents',
      });
    }
  }

  if (clarificationsResult.status === 'fulfilled') {
    const open: Clarification[] = clarificationsResult.value.filter(
      (clarification) => clarification.status === 'open',
    );
    if (open.length > 0) {
      items.push({
        label: `Open questions from your firm (${open.length})`,
        detail: open[0]?.question ?? null,
        href: '/family/help#clarifications',
      });
    }
  } else if (!isNotStarted(clarificationsResult.reason)) {
    partial = true;
  }

  if (checklistResult.status === 'fulfilled') {
    const checklist: SigningChecklist = checklistResult.value;
    const attested = new Set(checklist.attestations.map((a) => a.requirement_id));
    const unsigned = checklist.items.filter((item) => !attested.has(item.key));
    if (unsigned.length > 0) {
      items.push({
        label: 'Signing items to attest',
        detail: `${unsigned.length} of ${checklist.items.length} checklist items unsigned`,
        href: '/family/signing',
      });
    }
  } else if (!isNotStarted(checklistResult.reason)) {
    partial = true;
  }

  if (fundingResult.status === 'fulfilled') {
    const tasks: FundingTask[] = fundingResult.value;
    const incomplete = tasks.filter((task) => task.status !== 'complete');
    if (incomplete.length > 0) {
      items.push({
        label: 'Incomplete funding tasks',
        detail: `${incomplete.length} of ${tasks.length} tasks still open`,
        href: '/family/funding',
      });
    }
  } else if (!isNotStarted(fundingResult.reason)) {
    partial = true;
  }

  NOMINATION_TYPES.forEach((type, index) => {
    const result = settled[5 + index];
    if (result?.status === 'rejected') {
      if (!isNotStarted(result.reason)) partial = true;
      return;
    }
    if (result?.status === 'fulfilled' && Array.isArray(result.value) && result.value.length === 0) {
      items.push({
        label: `Name ${NOMINATION_LABELS[type] === 'guardian' ? 'a guardian' : `an ${NOMINATION_LABELS[type]}`}`,
        detail: `No ${NOMINATION_LABELS[type]} nominated yet`,
        href: '/family/guardians',
      });
    }
  });

  return { matter, items, partial };
}

function MatterCheckupCard({ checkup }: { checkup: MatterCheckup }) {
  const { matter, items } = checkup;
  const clear = items.length === 0;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle>{matter.title}</CardTitle>
            <CardDescription>
              {matter.household?.name ?? 'Household'} · {matter.jurisdiction_code}
            </CardDescription>
          </div>
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
              clear
                ? 'bg-lf-success-100 text-lf-success-700'
                : 'bg-lf-accent-100 text-lf-accent-800',
            )}
          >
            {clear ? (
              <CheckCircle2 className="size-3.5" aria-hidden="true" />
            ) : (
              <CircleAlert className="size-3.5" aria-hidden="true" />
            )}
            {clear ? 'All clear' : `${items.length} to review`}
          </span>
        </div>
      </CardHeader>
      <CardContent>
        {clear ? (
          <p className="text-sm text-lf-text-muted">
            Nothing is waiting on you for this matter. We&apos;ll flag anything new here.
          </p>
        ) : (
          <ul className="divide-y divide-lf-border">
            {items.map((item) => (
              <li key={item.label}>
                <Link
                  href={item.href}
                  className="flex min-h-11 items-center justify-between gap-3 rounded-lf-control py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
                >
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-lf-text">{item.label}</span>
                    {item.detail && (
                      <span className="block truncate text-xs text-lf-text-subtle">
                        {item.detail}
                      </span>
                    )}
                  </span>
                  <ArrowRight className="size-4 shrink-0 text-lf-primary-700" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        )}
        {checkup.partial && (
          <p className="mt-3 text-xs text-lf-text-subtle">
            Some sections could not be checked — what is shown may be incomplete.
          </p>
        )}
        <div className="mt-3">
          <span
            className={cn(
              'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
              statusBadgeClass(matter.status),
            )}
          >
            {formatStatus(matter.status)}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

/** Checkup — per-matter plan health: what needs attention, with links that resolve it. */
export default function FamilyCheckupPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [checkups, setCheckups] = useState<MatterCheckup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async (list: MatterSummary[]) => {
    setLoading(true);
    setError(null);
    try {
      // PERF-017 (partial, by design): each matter's checkup fires ~8
      // API calls, and this page previously ran them for ALL matters at
      // once — an uncapped burst that grows with the matter count.
      // Capping at 2 matters in flight bounds the burst; it does not
      // reduce the per-matter call count. The real fix is a server-side
      // checkup aggregate endpoint, which is out of scope here.
      const results = await mapWithConcurrency(list, 2, (matter) => buildMatterCheckup(matter));
      setCheckups(results);
    } catch (err: unknown) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!mattersLoading && !mattersError && matters.length > 0) {
      void load(matters);
    }
  }, [matters, mattersLoading, mattersError, load]);

  const retry = useCallback(() => {
    if (mattersError) reloadMatters();
    else void load(matters);
  }, [mattersError, reloadMatters, load, matters]);

  const totalOpen = useMemo(
    () => checkups.reduce((sum, checkup) => sum + checkup.items.length, 0),
    [checkups],
  );

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Checkup</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Your plan health, per matter. Every item links to the page that resolves it.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={4} label="Loading your checkup…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={retry} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your plan health checklist will appear here once your firm starts a matter for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && loading && (
        <LoadingState lines={5} label="Checking your plan health…" />
      )}
      {error != null && !loading ? <ErrorState error={error} onRetry={retry} /> : null}

      {!loading && !error && checkups.length > 0 && (
        <p
          role="status"
          className={cn(
            'flex items-start gap-2 rounded-lf-control border px-4 py-3 text-sm',
            totalOpen === 0
              ? 'border-lf-success-600 bg-lf-success-50 text-lf-success-700'
              : 'border-lf-accent-600 bg-lf-accent-50 text-lf-accent-800',
          )}
        >
          {totalOpen === 0 ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          ) : (
            <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          )}
          {totalOpen === 0
            ? 'Everything looks healthy across your matters.'
            : `${totalOpen} ${totalOpen === 1 ? 'item needs' : 'items need'} your attention across ${checkups.length} ${checkups.length === 1 ? 'matter' : 'matters'}.`}
        </p>
      )}

      {!loading && !error && checkups.length > 0 && (
        <div className="space-y-6">
          {checkups.map((checkup) => (
            <MatterCheckupCard key={checkup.matter.id} checkup={checkup} />
          ))}
        </div>
      )}
    </div>
  );
}
