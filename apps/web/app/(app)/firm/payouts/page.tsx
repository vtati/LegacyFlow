'use client';

import { useCallback, useEffect, useState } from 'react';
import { Inbox } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import {
  payoutApi,
  type CaseRequest,
  type Payout,
} from '@/lib/api-client';
import { formatDateTime, formatMoney } from '@/lib/format';
import { cn } from '@/lib/utils';

function payoutStatusClass(status: string): string {
  if (status === 'paid') return 'bg-emerald-50 text-emerald-700';
  if (status === 'approved') return 'bg-indigo-50 text-indigo-700';
  if (status === 'disputed') return 'bg-red-50 text-red-700';
  return 'bg-amber-50 text-amber-700';
}

function PayoutCard({
  payout,
  onApprove,
  onPay,
  onDispute,
  acting,
}: {
  payout: Payout;
  onApprove: () => void;
  onPay: (reference: string | null) => void;
  onDispute: (reason: string) => void;
  acting: boolean;
}): React.JSX.Element {
  const [showPay, setShowPay] = useState(false);
  const [reference, setReference] = useState('');
  const [showDispute, setShowDispute] = useState(false);
  const [reason, setReason] = useState('');

  return (
    <Card>
      <CardContent className="space-y-3 p-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-base font-semibold text-lf-text">
              {payout.attorney_name ?? 'Attorney'}
            </p>
            <p className="mt-0.5 text-sm text-lf-text-muted">
              {payout.matter_title ?? 'Matter'} · {payout.tier === 'complex' ? 'Complex' : 'Standard'} review
            </p>
            <p className="mt-0.5 text-xs text-lf-text-subtle">
              Created {formatDateTime(payout.created_at)}
              {payout.paid_at ? ` · Paid ${formatDateTime(payout.paid_at)}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-lf-text">{formatMoney(payout.amount_cents)}</span>
            <span
              className={cn(
                'inline-block rounded-full px-2.5 py-1 text-xs font-medium capitalize',
                payoutStatusClass(payout.status),
              )}
            >
              {payout.status}
            </span>
          </div>
        </div>

        {payout.status === 'pending' && (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={acting} onClick={onApprove} className="lf-pressable">
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={acting}
              onClick={() => setShowDispute((v) => !v)}
              className="lf-pressable"
            >
              Dispute
            </Button>
          </div>
        )}
        {payout.status === 'approved' && (
          <div className="flex flex-wrap gap-2">
            {!showPay ? (
              <Button size="sm" disabled={acting} onClick={() => setShowPay(true)} className="lf-pressable">
                Mark paid
              </Button>
            ) : (
              <div className="flex w-full flex-wrap items-end gap-2">
                <div className="min-w-48 flex-1">
                  <Label htmlFor={`ref-${payout.id}`}>Transfer reference (optional)</Label>
                  <Input
                    id={`ref-${payout.id}`}
                    value={reference}
                    onChange={(e) => setReference(e.target.value)}
                    placeholder="Bank transfer memo"
                  />
                </div>
                <Button
                  size="sm"
                  disabled={acting}
                  onClick={() => onPay(reference.trim() || null)}
                  className="lf-pressable"
                >
                  Confirm paid
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setShowPay(false)}>
                  Cancel
                </Button>
              </div>
            )}
          </div>
        )}
        {showDispute && payout.status === 'pending' && (
          <div className="flex w-full flex-wrap items-end gap-2">
            <div className="min-w-48 flex-1">
              <Label htmlFor={`dispute-${payout.id}`}>Dispute reason</Label>
              <Input
                id={`dispute-${payout.id}`}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why is this payout disputed?"
              />
            </div>
            <Button
              size="sm"
              variant="destructive"
              disabled={acting || reason.trim().length === 0}
              onClick={() => onDispute(reason.trim())}
              className="lf-pressable"
            >
              Flag disputed
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowDispute(false)}>
              Cancel
            </Button>
          </div>
        )}
        {payout.status === 'disputed' && payout.dispute_reason && (
          <p className="rounded-lf-card bg-red-50 px-3 py-2 text-sm text-red-700">
            Disputed: {payout.dispute_reason}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Firm payouts — admin queue for approving and paying attorney payouts,
 * plus attorney case requests awaiting assignment decisions.
 */
export default function FirmPayoutsPage() {
  const [payouts, setPayouts] = useState<Payout[] | null>(null);
  const [requests, setRequests] = useState<CaseRequest[] | null>(null);
  const [filter, setFilter] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [p, r] = await Promise.all([
        payoutApi.listPayouts(filter || undefined),
        payoutApi.listCaseRequests('pending').catch(() => []),
      ]);
      setPayouts(p);
      setRequests(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load payouts.');
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = useCallback(
    async (id: string, fn: () => Promise<unknown>) => {
      setActingId(id);
      setError(null);
      try {
        await fn();
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Action failed.');
      } finally {
        setActingId(null);
      }
    },
    [load],
  );

  if (error && payouts === null)
    return <ErrorState error={new Error(error)} onRetry={() => void load()} />;
  if (payouts === null) return <LoadingState label="Loading payouts…" />;

  const pendingCount = payouts.filter((p) => p.status === 'pending').length;

  return (
    <div className="space-y-6 lf-animate-fade-up">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Payouts</h1>
        <p className="text-sm text-lf-text-subtle">
          Approve completion payouts and mark them paid via bank transfer. Payouts are
          created automatically when a matter reaches plan implemented.
        </p>
      </div>

      {error && (
        <p role="alert" className="rounded-lf-card bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {['', 'pending', 'approved', 'paid', 'disputed'].map((s) => (
          <Button
            key={s}
            size="sm"
            variant={filter === s ? 'default' : 'outline'}
            onClick={() => setFilter(s)}
            className="lf-pressable"
          >
            {s === '' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
            {s === 'pending' && pendingCount > 0 ? ` (${pendingCount})` : ''}
          </Button>
        ))}
      </div>

      {payouts.length === 0 ? (
        <EmptyState
          title="No payouts yet"
          description="Payouts appear here when an assigned matter reaches plan implemented."
        />
      ) : (
        <div className="grid gap-4">
          {payouts.map((p) => (
            <PayoutCard
              key={p.id}
              payout={p}
              acting={actingId === p.id}
              onApprove={() => void act(p.id, () => payoutApi.approvePayout(p.id))}
              onPay={(ref) => void act(p.id, () => payoutApi.payPayout(p.id, ref))}
              onDispute={(reason) => void act(p.id, () => payoutApi.disputePayout(p.id, reason))}
            />
          ))}
        </div>
      )}

      {/* Case requests */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Inbox className="h-5 w-5 text-lf-primary" /> Case requests
          </CardTitle>
          <CardDescription>
            Attorneys requesting unassigned cases. Accept a request, then assign the
            matter from the matter detail page.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!requests || requests.length === 0 ? (
            <p className="text-sm text-lf-text-subtle">No pending case requests.</p>
          ) : (
            <ul className="divide-y divide-lf-border">
              {requests.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                  <div>
                    <p className="text-sm font-medium text-lf-text">
                      {r.attorney_name ?? 'Attorney'} → {r.matter_title ?? 'Matter'}
                    </p>
                    <p className="text-xs text-lf-text-subtle">
                      Requested {formatDateTime(r.created_at)}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      disabled={actingId === r.id}
                      onClick={() => void act(r.id, () => payoutApi.decideCaseRequest(r.id, 'accepted'))}
                      className="lf-pressable"
                    >
                      Accept
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={actingId === r.id}
                      onClick={() => void act(r.id, () => payoutApi.decideCaseRequest(r.id, 'declined'))}
                      className="lf-pressable"
                    >
                      Decline
                    </Button>
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
