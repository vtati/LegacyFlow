'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type MatterSummary, type SigningChecklist } from '@/lib/api-client';
import { fetchAllMatters } from '@/lib/firm-matters';
import { formatDateTime, formatStatus } from '@/lib/format';
import { cn } from '@/lib/utils';

interface SigningRow {
  matter: MatterSummary;
  checklist: SigningChecklist | null;
}

function attestedIds(checklist: SigningChecklist): Set<string> {
  return new Set(checklist.attestations.map((a) => a.requirement_id));
}

function SigningCard({ row }: { row: SigningRow }) {
  const { matter, checklist } = row;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          <Link
            href={`/firm/matters/${matter.id}`}
            className="text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
          >
            {matter.title || 'Untitled matter'}
          </Link>
        </CardTitle>
        <CardDescription>
          {matter.household?.name ?? 'Household'} · {formatStatus(matter.status)}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {checklist == null ? (
          <p className="text-sm text-lf-text-muted">
            No signing checklist is available for this matter yet — the checklist fetch failed or
            the matter has not reached signing.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
              <span className="text-lf-text-muted">
                Document: <span className="text-lf-text">{checklist.document_type}</span>
              </span>
              <span className="text-lf-text-muted">
                Completed:{' '}
                <span className="text-lf-text">
                  {checklist.completed_at ? formatDateTime(checklist.completed_at) : 'Not yet'}
                </span>
              </span>
              <span className="text-lf-text-muted">
                Attestations:{' '}
                <span className="text-lf-text">
                  {attestedIds(checklist).size} of {checklist.items.length}
                </span>
              </span>
            </div>
            {checklist.items.length === 0 ? (
              <p className="text-sm text-lf-text-muted">The checklist has no requirements yet.</p>
            ) : (
              <ul className="space-y-2">
                {checklist.items.map((item) => {
                  const signed = attestedIds(checklist).has(item.key);
                  return (
                    <li
                      key={item.key}
                      className="flex items-start justify-between gap-3 rounded-lf-control border border-lf-border px-3 py-2.5"
                    >
                      <span>
                        <span className="block text-sm font-medium text-lf-text">{item.label}</span>
                        {item.detail && (
                          <span className="block text-sm text-lf-text-muted">{item.detail}</span>
                        )}
                      </span>
                      <span
                        className={cn(
                          'inline-block shrink-0 rounded-full px-2.5 py-1 text-xs font-medium',
                          signed
                            ? 'bg-lf-success-100 text-lf-success-700'
                            : 'bg-lf-accent-100 text-lf-accent-800',
                        )}
                      >
                        {signed ? 'Signed' : 'Pending'}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Firm signing: cross-matter view of signing checklists and signer status. */
export default function FirmSigningPage(): React.JSX.Element {
  const [rows, setRows] = useState<SigningRow[]>([]);
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
              .getChecklist(matter.id)
              .then((checklist) => ({ matter, checklist }) as SigningRow)
              .catch(() => ({ matter, checklist: null }) as SigningRow),
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

  if (loading) return <LoadingState lines={6} label="Loading signing checklists…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;

  const withChecklist = rows.filter((r) => r.checklist != null);
  const totalItems = withChecklist.reduce((sum, r) => sum + (r.checklist?.items.length ?? 0), 0);
  const totalSigned = withChecklist.reduce(
    (sum, r) => sum + (r.checklist ? attestedIds(r.checklist).size : 0),
    0,
  );
  const completed = withChecklist.filter((r) => r.checklist?.completed_at != null).length;

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Signing</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Client-reported execution across every matter: signer status per checklist requirement.
        </p>
      </div>

      <section aria-label="Signing summary" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { label: 'Matters tracked', count: rows.length },
          { label: 'With checklist', count: withChecklist.length },
          { label: 'Requirements signed', count: totalSigned },
          { label: 'Pending requirements', count: Math.max(0, totalItems - totalSigned) },
          { label: 'Checklists completed', count: completed },
        ].map((card) => (
          <Card key={card.label}>
            <CardContent className="p-6">
              <p className="text-3xl font-bold tabular-nums text-lf-text">{card.count}</p>
              <p className="mt-1 text-sm text-lf-text-muted">{card.label}</p>
            </CardContent>
          </Card>
        ))}
      </section>

      {rows.length === 0 ? (
        <EmptyState
          title="No matters yet"
          description="Signing checklists appear here once the firm has matters."
        />
      ) : (
        <div className="space-y-4">
          {rows.map((row) => (
            <SigningCard key={row.matter.id} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}
