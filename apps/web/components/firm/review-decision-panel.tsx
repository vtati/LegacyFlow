'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { firmApi } from '@/lib/api-client';
import { formatStatus } from '@/lib/format';

/**
 * Attorney decision panel — the three primary review actions.
 * - Approve: approves the pending intake_approval gate (creating one if needed)
 *   and moves the matter to APPROVED_FOR_ASSEMBLY.
 * - Request clarification: posts a structured question and moves the matter to
 *   CLARIFICATION_REQUESTED.
 * - Escalate: moves the matter to ESCALATED for senior review.
 */
export function ReviewDecisionPanel({
  matterId,
  status,
  onStatusChange,
}: {
  matterId: string;
  status: string;
  onStatusChange: (newStatus: string) => void;
}): React.JSX.Element {
  const [notes, setNotes] = useState('');
  const [question, setQuestion] = useState('');
  const [mode, setMode] = useState<'none' | 'clarify'>('none');
  const [acting, setActing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const actionable = status === 'ATTORNEY_REVIEW' || status === 'INTAKE_SUBMITTED';

  const handleApprove = async (): Promise<void> => {
    setError(null);
    setActing('approve');
    try {
      // Ensure an intake_approval gate exists, then approve it.
      const gates = await firmApi.listGates(matterId);
      let gate = gates.find(
        (g) => g.gate_type === 'intake_approval' && g.status === 'pending',
      );
      if (!gate) {
        gate = await firmApi.createGate(matterId, {
          gate_type: 'intake_approval',
          notes: notes.trim() || null,
        });
      }
      await firmApi.decideGate(gate.id, {
        decision: 'approved',
        notes: notes.trim() || null,
      });
      const updated = await firmApi.transitionMatter(
        matterId,
        'APPROVED_FOR_ASSEMBLY',
        notes.trim() || null,
      );
      onStatusChange(updated.matter.status);
      setNotes('');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not approve.');
    } finally {
      setActing(null);
    }
  };

  const handleClarify = async (): Promise<void> => {
    if (question.trim().length === 0) {
      setError('Write the clarification question first.');
      return;
    }
    setError(null);
    setActing('clarify');
    try {
      await firmApi.createClarification(matterId, { question: question.trim() });
      const updated = await firmApi.transitionMatter(
        matterId,
        'CLARIFICATION_REQUESTED',
        question.trim().slice(0, 200),
      );
      onStatusChange(updated.matter.status);
      setQuestion('');
      setMode('none');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not request clarification.');
    } finally {
      setActing(null);
    }
  };

  const handleEscalate = async (): Promise<void> => {
    setError(null);
    setActing('escalate');
    try {
      const updated = await firmApi.transitionMatter(
        matterId,
        'ESCALATED',
        notes.trim() || 'Escalated by reviewing attorney.',
      );
      onStatusChange(updated.matter.status);
      setNotes('');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not escalate.');
    } finally {
      setActing(null);
    }
  };

  if (!actionable) {
    return (
      <Card>
        <CardContent className="p-5">
          <p className="text-sm text-lf-text-muted">
            Review decisions are available when the matter is in{' '}
            <span className="font-medium text-lf-text">Attorney review</span> or{' '}
            <span className="font-medium text-lf-text">Intake submitted</span>. Current
            status: <span className="font-medium text-lf-text">{formatStatus(status)}</span>.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-lf-primary-200">
      <CardContent className="space-y-4 p-5">
        <div>
          <h3 className="text-base font-semibold text-lf-text">Attorney decision</h3>
          <p className="mt-1 text-sm text-lf-text-muted">
            Your decision is recorded and the matter moves to the next step automatically.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="decision-notes">Notes (optional)</Label>
          <textarea
            id="decision-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            maxLength={2000}
            placeholder="Context for the record…"
            className="flex w-full rounded-lf-control border border-lf-border bg-white px-3 py-2 text-sm text-lf-text placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          />
        </div>

        {mode === 'clarify' && (
          <div className="space-y-1.5 rounded-lf-control border border-lf-border bg-lf-canvas-subtle p-4 lf-animate-fade-up">
            <Label htmlFor="clarify-question">Clarification question</Label>
            <textarea
              id="clarify-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={3}
              maxLength={2000}
              placeholder="What do you need the family to clarify?"
              className="flex w-full rounded-lf-control border border-lf-border bg-white px-3 py-2 text-sm text-lf-text placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
            />
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                disabled={acting != null}
                onClick={handleClarify}
              >
                {acting === 'clarify' ? 'Sending…' : 'Send clarification'}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={acting != null}
                onClick={() => setMode('none')}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}

        {error && (
          <p role="alert" className="text-sm text-lf-error-700">
            {error}
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            disabled={acting != null}
            onClick={handleApprove}
            className="lf-pressable"
          >
            {acting === 'approve' ? 'Approving…' : 'Approve'}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={acting != null}
            onClick={() => setMode(mode === 'clarify' ? 'none' : 'clarify')}
            className="lf-pressable"
          >
            Request clarification
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={acting != null}
            onClick={handleEscalate}
            className="lf-pressable border-lf-error-300 text-lf-error-700 hover:bg-lf-error-50"
          >
            {acting === 'escalate' ? 'Escalating…' : 'Escalate'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
