'use client';

import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { ApiError, adminApi, type ContentPack } from '@/lib/api-client';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Responsive table cells (presentation only): below the md breakpoint each
 * table row becomes a stacked label/value card. Labels come from the cell's
 * data-th attribute. Same table, same data, no behavior change.
 */
const MOBILE_TABLE_CELL =
  'max-md:block max-md:border-0 max-md:px-0 max-md:py-1.5 max-md:before:mb-1 max-md:before:block max-md:before:text-[11px] max-md:before:font-semibold max-md:before:uppercase max-md:before:tracking-wider max-md:before:text-lf-text-subtle max-md:before:content-[attr(data-th)]';

/** Knowledge & rules: read-only reference of jurisdiction content packs. */
export default function FirmKnowledgeRulesPage(): React.JSX.Element {
  const [packs, setPacks] = useState<ContentPack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    adminApi
      .listPacks()
      .then((result) =>
        setPacks(
          [...result].sort((a, b) => a.jurisdiction_code.localeCompare(b.jurisdiction_code)),
        ),
      )
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) return <LoadingState lines={5} label="Loading content packs…" />;
  if (error != null) {
    if (error instanceof ApiError && error.isPermissionDenied) {
      return (
        <EmptyState
          title="Knowledge packs need firm admin access"
          description="Only firm admins can view the jurisdiction content pack reference."
        />
      );
    }
    return <ErrorState error={error} onRetry={load} />;
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Knowledge & rules</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Jurisdiction content packs powering the firm&rsquo;s intake questions, rules, and AI
          citations. Read-only reference — pack lifecycle is managed by platform admins.
        </p>
      </div>

      {packs.length === 0 ? (
        <EmptyState
          title="No content packs"
          description="No jurisdiction content packs have been published yet."
        />
      ) : (
        <Card>
          <div className="overflow-x-auto md:max-h-[75vh] md:overflow-y-auto">
            <table className="w-full text-left text-sm tabular-nums md:min-w-[680px]">
              <caption className="sr-only">Jurisdiction content packs</caption>
              <thead className="max-md:hidden md:sticky md:top-0 md:bg-lf-canvas-elevated">
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Jurisdiction
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Pack
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Version
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Effective from
                  </th>
                </tr>
              </thead>
              <tbody className="max-md:block max-md:space-y-3 max-md:bg-lf-canvas-subtle max-md:p-4">
                {packs.map((pack) => (
                  <tr key={pack.pack_id} className="border-t border-lf-border transition-colors hover:bg-lf-canvas-subtle max-md:block max-md:rounded-lf-card max-md:border max-md:border-lf-border max-md:bg-lf-canvas-elevated max-md:p-4">
                    <td data-th="Jurisdiction" className={`px-4 py-3 font-medium text-lf-text ${MOBILE_TABLE_CELL}`}>
                      {pack.jurisdiction_code}
                    </td>
                    <td data-th="Pack" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {pack.display_name}
                    </td>
                    <td data-th="Version" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>{pack.version}</td>
                    <td data-th="Status" className={`px-4 py-3 ${MOBILE_TABLE_CELL}`}>
                      <span
                        className={cn(
                          'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
                          statusBadgeClass(pack.status),
                        )}
                      >
                        {formatStatus(pack.status)}
                      </span>
                    </td>
                    <td data-th="Effective from" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {formatDate(pack.effective_from)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
