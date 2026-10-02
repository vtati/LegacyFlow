'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type MatterSummary, type MattersListResponse } from '@/lib/api-client';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Responsive table cells (presentation only): below the md breakpoint each
 * table row becomes a stacked label/value card. Labels come from the cell's
 * data-th attribute. Same table, same data, no behavior change.
 */
const MOBILE_TABLE_CELL =
  'max-md:block max-md:border-0 max-md:px-0 max-md:py-1.5 max-md:before:mb-1 max-md:before:block max-md:before:text-[11px] max-md:before:font-semibold max-md:before:uppercase max-md:before:tracking-wider max-md:before:text-lf-text-subtle max-md:before:content-[attr(data-th)]';

const PAGE_SIZE = 20;

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'intake', label: 'Intake' },
  { value: 'attorney_review', label: 'Attorney review' },
  { value: 'approved_for_assembly', label: 'Approved for assembly' },
  { value: 'released', label: 'Released' },
  { value: 'signing', label: 'Signing' },
  { value: 'funding', label: 'Funding' },
  { value: 'plan_implemented', label: 'Plan implemented' },
  { value: 'closed', label: 'Closed' },
];

interface FilterBarProps {
  q: string;
  onQueryChange: (value: string) => void;
  status: string;
  onStatusChange: (value: string) => void;
  jurisdiction: string;
  onJurisdictionChange: (value: string) => void;
}

function FilterBar({
  q,
  onQueryChange,
  status,
  onStatusChange,
  jurisdiction,
  onJurisdictionChange,
}: FilterBarProps) {
  return (
    <form
      role="search"
      aria-label="Search matters"
      className="grid gap-4 sm:grid-cols-3"
      onSubmit={(event) => event.preventDefault()}
    >
      <div className="space-y-1.5">
        <Label htmlFor="matters-search">Search</Label>
        <Input
          id="matters-search"
          type="search"
          value={q}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Client, household, or title…"
          autoComplete="off"
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="matters-status">Status</Label>
        <select
          id="matters-status"
          value={status}
          onChange={(event) => onStatusChange(event.target.value)}
          className="h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="matters-jurisdiction">Jurisdiction</Label>
        <Input
          id="matters-jurisdiction"
          value={jurisdiction}
          onChange={(event) => onJurisdictionChange(event.target.value)}
          placeholder="e.g. US-CA"
          autoComplete="off"
        />
      </div>
    </form>
  );
}

function MatterRow({ matter }: { matter: MatterSummary }) {
  return (
    <tr className="border-t border-lf-border transition-colors hover:bg-lf-canvas-subtle max-md:block max-md:rounded-lf-card max-md:border max-md:border-lf-border max-md:bg-white max-md:p-4">
      <td data-th="Client / household" className={`px-4 py-3 ${MOBILE_TABLE_CELL}`}>
        <Link
          href={`/firm/matters/${matter.id}`}
          className="font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
        >
          {matter.title || 'Untitled matter'}
        </Link>
        <p className="mt-0.5 text-sm text-lf-text-muted">{matter.household.name}</p>
      </td>
      <td data-th="Jurisdiction" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>{matter.jurisdiction_code}</td>
      <td data-th="Status" className={`px-4 py-3 ${MOBILE_TABLE_CELL}`}>
        <span
          className={cn(
            'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
            statusBadgeClass(matter.status),
          )}
        >
          {formatStatus(matter.status)}
        </span>
      </td>
      <td data-th="Updated" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>{formatDate(matter.updated_at)}</td>
    </tr>
  );
}

/** Firm matters — searchable, filterable, paginated list of client matters. */
export default function FirmMattersPage(): React.JSX.Element {
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [status, setStatus] = useState('');
  const [jurisdiction, setJurisdiction] = useState('');
  const [page, setPage] = useState(1);
  const [response, setResponse] = useState<MattersListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQ(q), 400);
    return () => window.clearTimeout(timer);
  }, [q]);

  const load = useCallback(
    (pageToLoad: number) => {
      setLoading(true);
      setError(null);
      firmApi
        .listMatters({
          q: debouncedQ || undefined,
          status: status || undefined,
          jurisdictionCode: jurisdiction || undefined,
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
    [debouncedQ, status, jurisdiction],
  );

  useEffect(() => {
    load(1);
  }, [load]);

  const totalPages = response ? Math.max(1, Math.ceil(response.total / response.page_size)) : 1;
  const hasFilters = debouncedQ.trim() !== '' || status !== '' || jurisdiction.trim() !== '';

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Matters</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Every client matter at the firm. Search, filter, and open a matter to review it.
        </p>
      </div>

      <FilterBar
        q={q}
        onQueryChange={setQ}
        status={status}
        onStatusChange={setStatus}
        jurisdiction={jurisdiction}
        onJurisdictionChange={setJurisdiction}
      />

      {loading && <LoadingState lines={5} label="Loading matters…" />}

      {error != null && !loading ? <ErrorState error={error} onRetry={() => load(page)} /> : null}

      {!loading && !error && response && response.items.length === 0 && (
        <EmptyState
          title={hasFilters ? 'No matters match your filters' : 'No matters yet'}
          description={
            hasFilters
              ? 'Try a different search term, status, or jurisdiction.'
              : 'Matters appear here once the firm starts a plan for a household.'
          }
        />
      )}

      {!loading && !error && response && response.items.length > 0 && (
        <Card>
          <div className="overflow-x-auto md:max-h-[75vh] md:overflow-y-auto">
            <table className="w-full text-left text-sm tabular-nums md:min-w-[640px]">
              <caption className="sr-only">Client matters</caption>
              <thead className="max-md:hidden md:sticky md:top-0 md:bg-white">
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Client / household
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Jurisdiction
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Updated
                  </th>
                </tr>
              </thead>
              <tbody className="max-md:block max-md:space-y-3 max-md:bg-lf-canvas-subtle max-md:p-4">
                {response.items.map((matter) => (
                  <MatterRow key={matter.id} matter={matter} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {!loading && !error && response && totalPages > 1 && (
        <nav aria-label="Matters pagination" className="flex items-center justify-between gap-3">
          <p className="text-sm text-lf-text-muted">
            Page {response.page} of {totalPages} · {response.total} matters
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={response.page <= 1}
              onClick={() => load(response.page - 1)}
            >
              Previous
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={response.page >= totalPages}
              onClick={() => load(response.page + 1)}
            >
              Next
            </Button>
          </div>
        </nav>
      )}

      {!loading && !error && response && totalPages <= 1 && response.items.length > 0 && (
        <p className="text-sm text-lf-text-muted">
          {response.total} {response.total === 1 ? 'matter' : 'matters'}
        </p>
      )}
    </div>
  );
}
