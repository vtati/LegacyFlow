'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { attorneyApi, type AttorneyApplication } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';
import { cn } from '@/lib/utils';

function statusClass(status: string): string {
  if (status === 'approved') return 'bg-lf-success-100 text-lf-success-700';
  if (status === 'rejected') return 'bg-lf-error-100 text-lf-error-700';
  return 'bg-lf-primary-100 text-lf-primary-700';
}

function ApplicationCard({
  application,
  onApprove,
  onReject,
  acting,
}: {
  application: AttorneyApplication;
  onApprove: () => void;
  onReject: (reason: string) => void;
  acting: boolean;
}): React.JSX.Element {
  const [showReject, setShowReject] = useState(false);
  const [reason, setReason] = useState('');
  const [rejectError, setRejectError] = useState<string | null>(null);
  const pending = application.status === 'pending';

  const handleReject = (): void => {
    if (reason.trim().length === 0) {
      setRejectError('Add a reason so the applicant knows what to fix.');
      return;
    }
    setRejectError(null);
    onReject(reason.trim());
  };

  const docs = application.document_urls as Record<string, string>;

  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-base font-semibold text-lf-text">{application.full_name}</p>
            <p className="mt-0.5 text-sm text-lf-text-muted">
              {application.email}
              {application.phone ? ` · ${application.phone}` : ''}
            </p>
          </div>
          <span
            className={cn(
              'inline-block rounded-full px-2.5 py-1 text-xs font-medium capitalize',
              statusClass(application.status),
            )}
          >
            {application.status}
          </span>
        </div>

        <dl className="grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="font-medium text-lf-text-subtle">Bar number</dt>
            <dd className="text-lf-text">{application.bar_number}</dd>
          </div>
          <div>
            <dt className="font-medium text-lf-text-subtle">Jurisdictions</dt>
            <dd className="text-lf-text">{application.jurisdictions.join(', ') || '—'}</dd>
          </div>
          {application.years_experience != null && (
            <div>
              <dt className="font-medium text-lf-text-subtle">Experience</dt>
              <dd className="text-lf-text">{application.years_experience} years</dd>
            </div>
          )}
          {application.practice_areas && (
            <div>
              <dt className="font-medium text-lf-text-subtle">Practice areas</dt>
              <dd className="text-lf-text">{application.practice_areas}</dd>
            </div>
          )}
          <div>
            <dt className="font-medium text-lf-text-subtle">Applied</dt>
            <dd className="text-lf-text">{formatDateTime(application.created_at)}</dd>
          </div>
          {application.reviewed_at && (
            <div>
              <dt className="font-medium text-lf-text-subtle">Reviewed</dt>
              <dd className="text-lf-text">{formatDateTime(application.reviewed_at)}</dd>
            </div>
          )}
        </dl>

        {Object.keys(docs).length > 0 && (
          <div>
            <p className="text-sm font-medium text-lf-text-subtle">Documents</p>
            <ul className="mt-1 space-y-1">
              {Object.entries(docs).map(([key, url]) => (
                <li key={key}>
                  <a
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline"
                  >
                    {key.replace(/_/g, ' ')}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}

        {application.status === 'rejected' && application.rejection_reason && (
          <p className="rounded-lf-control bg-lf-error-50 p-3 text-sm text-lf-error-700">
            <span className="font-medium">Rejection reason: </span>
            {application.rejection_reason}
          </p>
        )}

        {pending && (
          <div className="space-y-3 border-t border-lf-border pt-4">
            {!showReject ? (
              <div className="flex flex-wrap gap-2">
                <Button type="button" size="sm" disabled={acting} onClick={onApprove}>
                  {acting ? 'Saving…' : 'Approve'}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={acting}
                  onClick={() => setShowReject(true)}
                >
                  Reject
                </Button>
              </div>
            ) : (
              <div className="space-y-2">
                <Label htmlFor={`reject-reason-${application.id}`}>Rejection reason</Label>
                <textarea
                  id={`reject-reason-${application.id}`}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  rows={2}
                  maxLength={2000}
                  placeholder="What should the applicant fix?"
                  className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
                />
                {rejectError && (
                  <p role="alert" className="text-sm text-lf-error-700">
                    {rejectError}
                  </p>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button type="button" size="sm" variant="destructive" disabled={acting} onClick={handleReject}>
                    Confirm rejection
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={acting}
                    onClick={() => setShowReject(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Firm admin: review and approve/reject attorney applications. */
export default function FirmAttorneysPage(): React.JSX.Element {
  const [applications, setApplications] = useState<AttorneyApplication[]>([]);
  const [filter, setFilter] = useState<'pending' | 'all'>('pending');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [actingId, setActingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    attorneyApi
      .listApplications(filter === 'pending' ? 'pending' : undefined)
      .then(setApplications)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  const handleApprove = (id: string): void => {
    setActingId(id);
    setActionError(null);
    attorneyApi
      .approveApplication(id)
      .then((updated) =>
        setApplications((prev) =>
          filter === 'pending'
            ? prev.filter((a) => a.id !== updated.id)
            : prev.map((a) => (a.id === updated.id ? updated : a)),
        ),
      )
      .catch((err: unknown) =>
        setActionError(err instanceof Error ? err.message : 'Could not approve.'),
      )
      .finally(() => setActingId(null));
  };

  const handleReject = (id: string, reason: string): void => {
    setActingId(id);
    setActionError(null);
    attorneyApi
      .rejectApplication(id, reason)
      .then((updated) =>
        setApplications((prev) =>
          filter === 'pending'
            ? prev.filter((a) => a.id !== updated.id)
            : prev.map((a) => (a.id === updated.id ? updated : a)),
        ),
      )
      .catch((err: unknown) =>
        setActionError(err instanceof Error ? err.message : 'Could not reject.'),
      )
      .finally(() => setActingId(null));
  };

  if (loading) return <LoadingState lines={4} label="Loading applications…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-lf-text">
            Attorney applications
          </h1>
          <p className="mt-2 text-sm text-lf-text-muted">
            Verify bar credentials and documents manually, then approve or reject.
            Approving grants the attorney role immediately.
          </p>
        </div>
        <div className="flex gap-1 rounded-lf-control border border-lf-border bg-white p-1">
          {(['pending', 'all'] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={cn(
                'rounded-lf-control px-3 py-1.5 text-sm font-medium capitalize transition-colors',
                filter === f
                  ? 'bg-lf-primary-600 text-white'
                  : 'text-lf-text-muted hover:text-lf-text',
              )}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {actionError && (
        <p role="alert" className="text-sm text-lf-error-700">
          {actionError}
        </p>
      )}

      {applications.length === 0 ? (
        <EmptyState
          title={filter === 'pending' ? 'No pending applications' : 'No applications yet'}
          description="New attorney applications will appear here for manual review."
        />
      ) : (
        <ul className="space-y-4">
          {applications.map((app) => (
            <li key={app.id} className="lf-animate-fade-up">
              <ApplicationCard
                application={app}
                acting={actingId === app.id}
                onApprove={() => handleApprove(app.id)}
                onReject={(reason) => handleReject(app.id, reason)}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
