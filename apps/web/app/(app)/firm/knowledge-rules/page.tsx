'use client';

import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { ApiError, adminApi, type ContentPack } from '@/lib/api-client';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

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
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Knowledge & rules</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
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
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <caption className="sr-only">Jurisdiction content packs</caption>
              <thead>
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
              <tbody>
                {packs.map((pack) => (
                  <tr key={pack.pack_id} className="border-t border-lf-border">
                    <td className="px-4 py-3 font-medium text-lf-text">
                      {pack.jurisdiction_code}
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {pack.display_name}
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">{pack.version}</td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
                          statusBadgeClass(pack.status),
                        )}
                      >
                        {formatStatus(pack.status)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
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
