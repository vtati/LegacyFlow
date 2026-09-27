'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiError, api, type MatterSummary } from '@/lib/api-client';

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
            {matter.title} — {matter.household.name}
          </option>
        ))}
      </select>
      {selected && (
        <p className="text-xs text-lf-text-subtle">
          {selected.household.name} · {formatStatus(selected.status)}
        </p>
      )}
    </div>
  );
}

export function formatStatus(status: string): string {
  return status
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}
