'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiError, api, type MatterSummary } from '@/lib/api-client';
import { formatStatus } from '@/lib/format';

interface UseMattersResult {
  matters: MatterSummary[];
  loading: boolean;
  error: unknown;
  reload: () => void;
}

/** Load the family's matters once; shared by profile, dependents, guardians, intake pages. */
export function useMatters(): UseMattersResult {
  const [matters, setMatters] = useState<MatterSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .listMatters()
      .then((response) => setMatters(response.items))
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return { matters, loading, error, reload: load };
}

/**
 * Resolve each matter's coverage from the LIVE intake-progress endpoint.
 *
 * The stored `completeness_pct` on a matter summary is a lifecycle proxy
 * (the API sets it from the matter's status at creation and on every
 * status transition), so it routinely disagrees with the answer-driven
 * figure — a fresh matter showed 9% stored while its live progress was
 * 0%. Any surface that shows coverage must use this resolver so the same
 * matter never displays two percentages at once. Until a matter's live
 * figure lands (or if its fetch fails), the stored value is the
 * fallback — the same fallback for every number on the screen.
 */
export function useLiveCoverage(
  matters: MatterSummary[],
): (matter: MatterSummary) => number {
  const [liveById, setLiveById] = useState<Record<string, number>>({});

  useEffect(() => {
    if (matters.length === 0) return;
    let cancelled = false;
    for (const matter of matters) {
      api
        .getIntakeProgress(matter.id)
        .then((progress) => {
          if (!cancelled) {
            setLiveById((prev) => ({
              ...prev,
              [matter.id]: Math.round(progress.completeness_pct),
            }));
          }
        })
        .catch(() => {
          // Keep the stored fallback for this matter.
        });
    }
    return () => {
      cancelled = true;
    };
  }, [matters]);

  return useCallback(
    (matter: MatterSummary) => liveById[matter.id] ?? matter.completeness_pct,
    [liveById],
  );
}

interface MatterSelectProps {
  matters: MatterSummary[];
  value: string | null;
  onChange: (matterId: string) => void;
  id?: string;
}

/** Matter picker for matter-scoped pages (profile, dependents, guardians, intake). */
export function MatterSelect({
  matters,
  value,
  onChange,
  id = 'matter-select',
}: MatterSelectProps) {
  const selected = matters.find((m) => m.id === value);
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-lf-text">
        Matter
      </label>
      <select
        id={id}
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full max-w-md rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
      >
        <option value="" disabled>
          Choose a matter…
        </option>
        {matters.map((matter) => (
          <option key={matter.id} value={matter.id}>
            {matter.title} — {matter.household?.name ?? 'Household'}
          </option>
        ))}
      </select>
      {selected && (
        <p className="text-xs text-lf-text-subtle">
          {selected.household?.name ?? 'Household'} · {formatStatus(selected.status)}
        </p>
      )}
    </div>
  );
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export { formatStatus };
