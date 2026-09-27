'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type Clarification } from '@/lib/api-client';
import { formatDateTime, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

function statusClass(status: string): string {
  if (status === 'answered') return 'bg-lf-success-100 text-lf-success-700';
  return statusBadgeClass('pending');
}

/** Clarifications tab: question list plus an attorney question composer. */
export function ClarificationsTab({ matterId }: { matterId: string }): React.JSX.Element {
  const [clarifications, setClarifications] = useState<Clarification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [question, setQuestion] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listClarifications(matterId)
      .then((result) =>
        setClarifications(
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

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (trimmed.length === 0) {
      setFormError('Write the question before sending it.');
      return;
    }
    if (trimmed.length > 2000) {
      setFormError('Questions are limited to 2000 characters.');
      return;
    }
    setSaving(true);
    setFormError(null);
    setNotice(null);
    firmApi
      .createClarification(matterId, { question: trimmed })
      .then((created) => {
        setClarifications((prev) => [created, ...prev]);
        setQuestion('');
        setNotice('Question sent to the family.');
      })
      .catch((err: unknown) =>
        setFormError(err instanceof Error ? err.message : 'Could not send the question.'),
      )
      .finally(() => setSaving(false));
  };

  if (loading) return <LoadingState lines={4} label="Loading clarifications…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="p-5">
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="clarification-question">Ask the family a question</Label>
              <textarea
                id="clarification-question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                rows={3}
                maxLength={2000}
                placeholder="e.g. Who should receive the coin collection if your primary beneficiary cannot?"
                className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              />
              <p className="text-xs text-lf-text-subtle">
                Questions appear in the family&apos;s workspace; their answer shows up here.
              </p>
            </div>
            {formError && (
              <p role="alert" className="text-sm text-lf-error-700">
                {formError}
              </p>
            )}
            <Button type="submit" disabled={saving}>
              {saving ? 'Sending…' : 'Send question'}
            </Button>
          </form>
        </CardContent>
      </Card>

      {notice && (
        <p role="status" className="text-sm text-lf-success-700">
          {notice}
        </p>
      )}

      {clarifications.length === 0 ? (
        <EmptyState
          title="No clarifications yet"
          description="Ask the family anything you need answered before the plan can move forward."
        />
      ) : (
        <ul className="space-y-3">
          {clarifications.map((clarification) => (
            <li key={clarification.id}>
              <Card>
                <CardContent className="space-y-2 p-5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span
                      className={cn(
                        'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
                        statusClass(clarification.status),
                      )}
                    >
                      {formatStatus(clarification.status)}
                    </span>
                    <time className="text-xs text-lf-text-subtle">
                      {formatDateTime(clarification.created_at)}
                    </time>
                  </div>
                  <p className="text-sm font-medium text-lf-text">{clarification.question}</p>
                  {clarification.response ? (
                    <div className="rounded-lf-control bg-lf-canvas-subtle px-3 py-2">
                      <p className="text-xs font-medium uppercase tracking-wide text-lf-text-subtle">
                        Family&apos;s answer
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-sm text-lf-text">
                        {clarification.response}
                      </p>
                      {clarification.answered_at && (
                        <p className="mt-1 text-xs text-lf-text-subtle">
                          Answered {formatDateTime(clarification.answered_at)}
                        </p>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-lf-text-muted">Awaiting an answer.</p>
                  )}
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
