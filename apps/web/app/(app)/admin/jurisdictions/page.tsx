'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState, LoadingState } from '@/components/family/page-state';
import { AdminErrorState, FirmScopeNote } from '@/components/admin/admin-page-state';
import { adminApi, type ContentPack } from '@/lib/api-client';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

interface JurisdictionGroup {
  jurisdiction_code: string;
  packs: ContentPack[];
}

/** Admin → Jurisdictions: jurisdictions derived client-side from your firm's content packs. */
export default function AdminJurisdictionsPage(): React.JSX.Element {
  const [packs, setPacks] = useState<ContentPack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    adminApi
      .listPacks()
      .then((result) => setPacks([...result]))
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const groups: JurisdictionGroup[] = useMemo(() => {
    const byCode = new Map<string, ContentPack[]>();
    for (const pack of packs) {
      const list = byCode.get(pack.jurisdiction_code) ?? [];
      list.push(pack);
      byCode.set(pack.jurisdiction_code, list);
    }
    return [...byCode.entries()]
      .map(([jurisdiction_code, items]) => ({
        jurisdiction_code,
        packs: items.sort((a, b) => b.version.localeCompare(a.version)),
      }))
      .sort((a, b) => a.jurisdiction_code.localeCompare(b.jurisdiction_code));
  }, [packs]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Jurisdictions</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Jurisdictions covered by the content packs available to your firm, with each
          pack&apos;s version and lifecycle status.
        </p>
      </div>

      <FirmScopeNote>
        These are the packs registered for your firm — manage their lifecycle under
        Content Packs.
      </FirmScopeNote>

      {loading && <LoadingState lines={4} label="Loading jurisdictions…" />}

      {error != null && !loading ? <AdminErrorState error={error} onRetry={load} /> : null}

      {!loading && !error && groups.length === 0 && (
        <EmptyState
          title="No jurisdictions yet"
          description="Jurisdictions appear here once your firm imports its first content pack."
        />
      )}

      {!loading && !error && groups.length > 0 && (
        <ul className="space-y-4">
          {groups.map((group) => (
            <li key={group.jurisdiction_code}>
              <Card>
                <CardContent className="space-y-3 p-5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="font-mono text-base font-semibold text-lf-text">
                      {group.jurisdiction_code}
                    </h2>
                    <p className="text-sm text-lf-text-muted">
                      {group.packs.length}{' '}
                      {group.packs.length === 1 ? 'pack' : 'packs'}
                    </p>
                  </div>
                  <ul className="divide-y divide-lf-border">
                    {group.packs.map((pack) => (
                      <li
                        key={`${pack.pack_id}:${pack.version}`}
                        className="flex flex-wrap items-center justify-between gap-2 py-2.5"
                      >
                        <div>
                          <p className="text-sm font-medium text-lf-text">{pack.display_name}</p>
                          <p className="mt-0.5 font-mono text-xs text-lf-text-muted">
                            {pack.pack_id} · v{pack.version}
                            {pack.effective_from
                              ? ` · effective ${formatDate(pack.effective_from)}`
                              : ''}
                          </p>
                        </div>
                        <span
                          className={cn(
                            'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
                            statusBadgeClass(pack.status),
                          )}
                        >
                          {formatStatus(pack.status)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
