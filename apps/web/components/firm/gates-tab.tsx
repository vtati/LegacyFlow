'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type Gate, type GateDecision } from '@/lib/api-client';
import { formatDateTime, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

function gateClass(status: string): string {
  if (status === 'approved') return 'bg-lf-success-100 text-lf-success-700';
  if (status === 'rejected') return 'bg-lf-error-100 text-lf-error-700';
  return statusBadgeClass('pending');
}

interface GateCardProps {
  gate: Gate;
  deciding: boolean;
  onDecide: (gateId: string, decision: GateDecision, notes: string | null) => void;
}

function GateCard({ gate, deciding, onDecide }: GateCardProps) {
  const [notes, setNotes] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);
  const pending = gate.status === 'pending';

  const handleDecide = (decision: GateDecision) => {
    if (decision === 'rejected' && notes.trim().length === 0) {
      setValidationError('Add a note explaining the rejection so the team knows what to fix.');
      return;
    }
    setValidationError(null);
    onDecide(gate.id, decision, notes.trim() || null);
  };

  return (
    <Card>
      <CardContent className="space-y-3 p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold text-lf-text">{formatStatus(gate.gate_type)}</p>
          <span
            className={cn(
              'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
              gateClass(gate.status),
            )}
          >
            {formatStatus(gate.status)}
          </span>
        </div>
        {gate.notes && (
          <p className="text-sm text-lf-text-muted">
            <span className="font-medium text-lf-text">Decision notes: </span>
            <span className="whitespace-pre-wrap">{gate.notes}</span>
          </p>
        )}

        {!pending && (
          <p className="text-sm text-lf-text-subtle">Decided {formatDateTime(gate.decided_at)}</p>
        )}

        {pending && (
          <div className="space-y-3 rounded-lf-control border border-lf-border bg-lf-canvas-subtle p-4">
            <div className="space-y-1.5">
              <Label htmlFor={`gate-notes-${gate.id}`}>Decision notes</Label>
              <textarea
                id={`gate-notes-${gate.id}`}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                rows={2}
                maxLength={2000}
                placeholder="Required when rejecting — what needs to change?"
                className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              />
            </div>
            {validationError && (
              <p role="alert" className="text-sm text-lf-error-700">
                {validationError}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                disabled={deciding}
                onClick={() => handleDecide('approved')}
              >
                {deciding ? 'Saving…' : 'Approve'}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={deciding}
                onClick={() => handleDecide('rejected')}
              >
                Reject
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Gates tab: approval gates with approve/reject actions for pending gates,
 * plus creation of a document_release gate when none is pending/approved.
 * Creation lives here as well as in the decision panel (R2): deciding alone
 * was a dead end when no gate existed yet, and release-production 409'd.
 * The API only allows creating/deciding a document_release gate while the
 * matter is APPROVED_FOR_ASSEMBLY, so the affordance is gated on
 * `matterStatus` when the caller provides it.
 */
export function GatesTab({
  matterId,
  matterStatus,
}: {
  matterId: string;
  matterStatus?: string;
}): React.JSX.Element {
  const [gates, setGates] = useState<Gate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listGates(matterId)
      .then((result) =>
        setGates(
          [...result].sort(
            (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
          ),
        ),
      )
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [matterId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleDecide = (gateId: string, decision: GateDecision, notes: string | null) => {
    setDecidingId(gateId);
    setActionError(null);
    firmApi
      .decideGate(gateId, { decision, notes })
      .then((updated) =>
        setGates((prev) => prev.map((gate) => (gate.id === updated.id ? updated : gate))),
      )
      .catch((err: unknown) =>
        setActionError(err instanceof Error ? err.message : 'Could not record the decision.'),
      )
      .finally(() => setDecidingId(null));
  };

  const handleCreateDocumentRelease = () => {
    setCreating(true);
    setActionError(null);
    firmApi
      .createGate(matterId, { gate_type: 'document_release', notes: null })
      .then((created) =>
        setGates((prev) =>
          [created, ...prev].sort(
            (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
          ),
        ),
      )
      .catch((err: unknown) =>
        setActionError(
          err instanceof Error ? err.message : 'Could not create the document release gate.',
        ),
      )
      .finally(() => setCreating(false));
  };

  if (loading) return <LoadingState lines={3} label="Loading approval gates…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;

  // A pending gate is decidable below and an approved one already satisfies
  // release-production; only offer creation when neither exists (a rejected
  // gate may be re-created — the API only conflicts on a pending duplicate).
  const hasUsableReleaseGate = gates.some(
    (g) =>
      g.gate_type === 'document_release' && (g.status === 'pending' || g.status === 'approved'),
  );
  const canCreateReleaseGate =
    !hasUsableReleaseGate &&
    (matterStatus === undefined || matterStatus === 'APPROVED_FOR_ASSEMBLY');

  return (
    <div className="space-y-4">
      {actionError && (
        <p role="alert" className="text-sm text-lf-error-700">
          {actionError}
        </p>
      )}
      {canCreateReleaseGate && (
        <Card>
          <CardContent className="space-y-3 p-5">
            <div>
              <p className="text-sm font-semibold text-lf-text">Document release gate</p>
              <p className="mt-1 text-sm text-lf-text-muted">
                Final documents cannot be released until the attorney approves a document
                release gate for this matter. Create the gate here, then approve it below.
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              disabled={creating}
              onClick={handleCreateDocumentRelease}
            >
              {creating ? 'Creating…' : 'Create document release gate'}
            </Button>
          </CardContent>
        </Card>
      )}
      {gates.length === 0 && !canCreateReleaseGate ? (
        <EmptyState
          title="No approval gates"
          description="Approval gates appear here when the plan reaches a step that needs attorney sign-off."
        />
      ) : gates.length === 0 ? null : (
        <ul className="space-y-3">
          {gates.map((gate) => (
            <li key={gate.id}>
              <GateCard gate={gate} deciding={decidingId === gate.id} onDecide={handleDecide} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
