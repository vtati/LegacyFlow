'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Briefcase, DollarSign, PauseCircle, PlayCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import {
  firmApi,
  getCurrentProfile,
  payoutApi,
  type MatterSummary,
  type Payout,
  type PayoutSummary,
} from '@/lib/api-client';
import { formatDate, formatMoney, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Attorney dashboard — assigned cases, earnings, and availability toggle.
 * API enforces the attorney role; 403s surface as an error state.
 */
export default function AttorneyDashboardPage() {
  const [cases, setCases] = useState<MatterSummary[] | null>(null);
  const [summary, setSummary] = useState<PayoutSummary | null>(null);
  const [payouts, setPayouts] = useState<Payout[] | null>(null);
  const [accepting, setAccepting] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const profile = await getCurrentProfile();
      const [mattersRes, sumRes, payoutsRes, availRes] = await Promise.all([
        firmApi.listMatters({ assignedTo: profile.id, pageSize: 100 }),
        payoutApi.myPayoutSummary().catch(() => null),
        payoutApi.listMyPayouts().catch(() => null),
        payoutApi.getAvailability().catch(() => null),
      ]);
      setSummary(sumRes);
      setPayouts(payoutsRes);
      setAccepting(availRes ? availRes.accepting_cases : null);
      setCases(mattersRes.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load dashboard.');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleAvailability = useCallback(async () => {
    if (accepting === null || toggling) return;
    setToggling(true);
    try {
      const res = await payoutApi.setAvailability(!accepting);
      setAccepting(res.accepting_cases);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update availability.');
    } finally {
      setToggling(false);
    }
  }, [accepting, toggling]);

  if (error) return <ErrorState error={new Error(error)} onRetry={() => void load()} />;
  if (cases === null) return <LoadingState label="Loading your dashboard…" />;

  return (
    <div className="space-y-6 lf-animate-fade-up">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-lf-text">Attorney dashboard</h1>
          <p className="text-sm text-lf-text-subtle">
            Your assigned cases, earnings, and availability.
          </p>
        </div>
        {accepting !== null && (
          <Button
            variant={accepting ? 'outline' : 'default'}
            onClick={() => void toggleAvailability()}
            disabled={toggling}
            className="lf-pressable"
          >
            {accepting ? (
              <>
                <PauseCircle className="mr-2 h-4 w-4" /> Pause new cases
              </>
            ) : (
              <>
                <PlayCircle className="mr-2 h-4 w-4" /> Accept new cases
              </>
            )}
          </Button>
        )}
      </div>

      {/* Earnings */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="lf-lift">
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1.5">
              <DollarSign className="h-3.5 w-3.5" /> Total earned
            </CardDescription>
            <CardTitle className="text-2xl">
              {formatMoney(summary?.total_earned_cents ?? 0)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card className="lf-lift">
          <CardHeader className="pb-2">
            <CardDescription>Pending</CardDescription>
            <CardTitle className="text-2xl">
              {formatMoney(summary?.pending_cents ?? 0)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card className="lf-lift">
          <CardHeader className="pb-2">
            <CardDescription>Approved</CardDescription>
            <CardTitle className="text-2xl">
              {formatMoney(summary?.approved_cents ?? 0)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card className="lf-lift">
          <CardHeader className="pb-2">
            <CardDescription>Paid out</CardDescription>
            <CardTitle className="text-2xl">{formatMoney(summary?.paid_cents ?? 0)}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      {/* My cases */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Briefcase className="h-5 w-5 text-lf-primary" /> My cases
          </CardTitle>
          <CardDescription>Matters currently assigned to you.</CardDescription>
        </CardHeader>
        <CardContent>
          {cases.length === 0 ? (
            <EmptyState
              title="No cases assigned yet"
              description="When the firm assigns you a matter it will appear here. You can also request unassigned cases from Opportunities."
              action={
                <Button asChild className="lf-pressable">
                  <Link href="/attorney/opportunities">Browse opportunities</Link>
                </Button>
              }
            />
          ) : (
            <ul className="divide-y divide-lf-border">
              {cases.map((m) => (
                <li key={m.id} className="py-3 first:pt-0 last:pb-0">
                  <Link
                    href={`/firm/matters/${m.id}`}
                    className="group flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium text-lf-text group-hover:text-lf-primary">
                        {m.title || 'Untitled matter'}
                      </p>
                      <p className="text-xs text-lf-text-subtle">
                        {m.household?.name ?? '—'} · {m.jurisdiction_code}
                      </p>
                    </div>
                    <span
                      className={cn(
                        'shrink-0 rounded-full px-2.5 py-1 text-xs font-medium',
                        statusBadgeClass(m.status),
                      )}
                    >
                      {formatStatus(m.status)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Payout history */}
      <Card>
        <CardHeader>
          <CardTitle>Payout history</CardTitle>
          <CardDescription>
            Completion payouts for implemented plans. Paid via bank transfer in v1.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!payouts || payouts.length === 0 ? (
            <p className="text-sm text-lf-text-subtle">
              No payouts yet. A payout is created when a matter you handled reaches
              plan implemented.
            </p>
          ) : (
            <ul className="divide-y divide-lf-border">
              {payouts.map((p) => (
                <li key={p.id} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-lf-text">
                      {p.matter_title ?? 'Matter'}
                    </p>
                    <p className="text-xs text-lf-text-subtle">
                      {formatDate(p.created_at)} · {p.tier === 'complex' ? 'Complex' : 'Standard'} review
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="text-sm font-semibold text-lf-text">
                      {formatMoney(p.amount_cents)}
                    </span>
                    <span
                      className={cn(
                        'rounded-full px-2.5 py-1 text-xs font-medium',
                        p.status === 'paid' && 'bg-emerald-50 text-emerald-700',
                        p.status === 'approved' && 'bg-indigo-50 text-indigo-700',
                        p.status === 'pending' && 'bg-amber-50 text-amber-700',
                        p.status === 'disputed' && 'bg-red-50 text-red-700',
                      )}
                    >
                      {p.status}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
