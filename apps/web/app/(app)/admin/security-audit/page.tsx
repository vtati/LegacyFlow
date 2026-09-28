'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { EmptyState, LoadingState } from '@/components/family/page-state';
import { AdminErrorState, FirmScopeNote } from '@/components/admin/admin-page-state';
import { adminApi, type AuditEventListResponse } from '@/lib/api-client';
import { formatDateTime, formatStatus } from '@/lib/format';

const PAGE_SIZE = 25;

interface AuditFilters {
  action: string;
  resourceType: string;
  actorType: string;
}

const EMPTY_FILTERS: AuditFilters = { action: '', resourceType: '', actorType: '' };

function truncateId(id: string | null): string {
  if (!id) return '—';
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

/** Admin → Security & Audit: your firm's audit trail with filters and pagination. */
export default function AdminSecurityAuditPage(): React.JSX.Element {
  const [draft, setDraft] = useState<AuditFilters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<AuditFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [response, setResponse] = useState<AuditEventListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(
    (pageToLoad: number, filters: AuditFilters) => {
      setLoading(true);
      setError(null);
      adminApi
        .listAuditEvents({
          action: filters.action.trim() || undefined,
          resourceType: filters.resourceType.trim() || undefined,
          actorType: filters.actorType.trim() || undefined,
          page: pageToLoad,
          pageSize: PAGE_SIZE,
        })
        .then((result) => {
          setResponse(result);
          setPage(result.page);
        })
        .catch((err: unknown) => setError(err))
        .finally(() => setLoading(false));
    },
    [],
  );

  useEffect(() => {
    load(1, EMPTY_FILTERS);
  }, [load]);

  const applyFilters = () => {
    setApplied(draft);
    setPage(1);
    load(1, draft);
  };

  const clearFilters = () => {
    setDraft(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setPage(1);
    load(1, EMPTY_FILTERS);
  };

  const totalPages = response ? Math.max(1, Math.ceil(response.total / response.page_size)) : 1;
  const hasFilters =
    applied.action.trim() !== '' ||
    applied.resourceType.trim() !== '' ||
    applied.actorType.trim() !== '';

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Security &amp; Audit</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Your firm&apos;s audit trail — who did what, on which resource, and when.
        </p>
      </div>

      <FirmScopeNote>
        Audit events are scoped to your firm. Matter and correlation IDs are truncated
        below; full identifiers are available to firm admins through the API.
      </FirmScopeNote>

      <Card>
        <div className="space-y-4 p-5">
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="audit-action">Action</Label>
              <Input
                id="audit-action"
                value={draft.action}
                onChange={(event) => setDraft((prev) => ({ ...prev, action: event.target.value }))}
                placeholder="e.g. packs.status_changed"
                autoComplete="off"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="audit-resource-type">Resource type</Label>
              <Input
                id="audit-resource-type"
                value={draft.resourceType}
                onChange={(event) =>
                  setDraft((prev) => ({ ...prev, resourceType: event.target.value }))
                }
                placeholder="e.g. content_pack"
                autoComplete="off"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="audit-actor-type">Actor type</Label>
              <Input
                id="audit-actor-type"
                value={draft.actorType}
                onChange={(event) =>
                  setDraft((prev) => ({ ...prev, actorType: event.target.value }))
                }
                placeholder="e.g. user"
                autoComplete="off"
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" className="min-h-[44px]" onClick={applyFilters}>
              Apply filters
            </Button>
            {hasFilters && (
              <Button
                type="button"
                variant="outline"
                className="min-h-[44px]"
                onClick={clearFilters}
              >
                Clear
              </Button>
            )}
          </div>
        </div>
      </Card>

      {loading && <LoadingState lines={6} label="Loading audit events…" />}

      {error != null && !loading ? (
        <AdminErrorState
          error={error}
          onRetry={() => load(page, applied)}
        />
      ) : null}

      {!loading && !error && response && response.items.length === 0 && (
        <EmptyState
          title={hasFilters ? 'No events match your filters' : 'No audit events yet'}
          description={
            hasFilters
              ? 'Try different filter values, or clear the filters to see the full trail.'
              : 'Audit events appear here as your firm uses LegacyFlow.'
          }
        />
      )}

      {!loading && !error && response && response.items.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <caption className="sr-only">Audit events</caption>
              <thead>
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Time
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Actor
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Action
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Resource
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Matter
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Correlation
                  </th>
                </tr>
              </thead>
              <tbody>
                {response.items.map((event) => (
                  <tr key={event.id} className="border-t border-lf-border">
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-lf-text-muted">
                      {formatDateTime(event.created_at)}
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {formatStatus(event.actor_type)}
                    </td>
                    <td className="px-4 py-3">
                      <code className="rounded-lf-control bg-lf-canvas-subtle px-2 py-1 text-xs text-lf-text">
                        {event.action}
                      </code>
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {formatStatus(event.resource_type)}
                    </td>
                    <td
                      className="px-4 py-3 font-mono text-xs text-lf-text-muted"
                      title={event.matter_id ?? ''}
                    >
                      {truncateId(event.matter_id)}
                    </td>
                    <td
                      className="px-4 py-3 font-mono text-xs text-lf-text-muted"
                      title={event.correlation_id ?? ''}
                    >
                      {truncateId(event.correlation_id)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {!loading && !error && response && (
        <nav aria-label="Audit events pagination" className="flex items-center justify-between gap-3">
          <p className="text-sm text-lf-text-muted">
            Page {response.page} of {totalPages} · {response.total} events
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              className="min-h-[44px]"
              disabled={response.page <= 1 || loading}
              onClick={() => load(response.page - 1, applied)}
            >
              Previous
            </Button>
            <Button
              type="button"
              variant="outline"
              className="min-h-[44px]"
              disabled={response.page >= totalPages || loading}
              onClick={() => load(response.page + 1, applied)}
            >
              Next
            </Button>
          </div>
        </nav>
      )}
    </div>
  );
}
