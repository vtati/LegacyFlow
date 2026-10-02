'use client';

import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { EmptyState, LoadingState } from '@/components/family/page-state';
import { AdminErrorState, FirmScopeNote } from '@/components/admin/admin-page-state';
import { adminApi, ApiError, type EffectiveFeatureFlag } from '@/lib/api-client';
import { cn } from '@/lib/utils';

/**
 * Responsive table cells (presentation only): below the md breakpoint each
 * table row becomes a stacked label/value card. Labels come from the cell's
 * data-th attribute. Same table, same data, no behavior change.
 */
const MOBILE_TABLE_CELL =
  'max-md:block max-md:border-0 max-md:px-0 max-md:py-1.5 max-md:before:mb-1 max-md:before:block max-md:before:text-[11px] max-md:before:font-semibold max-md:before:uppercase max-md:before:tracking-wider max-md:before:text-lf-text-subtle max-md:before:content-[attr(data-th)]';

function ToggleSwitch({
  checked,
  saving,
  disabled,
  onToggle,
  label,
}: {
  checked: boolean;
  saving: boolean;
  disabled: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled || saving}
      onClick={onToggle}
      className={cn(
        'relative inline-flex h-[44px] w-[72px] shrink-0 items-center rounded-full px-1 transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'justify-end bg-lf-success-600' : 'justify-start bg-lf-border',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'flex size-9 items-center justify-center rounded-full bg-white text-xs font-semibold shadow',
          saving ? 'text-lf-text-subtle' : checked ? 'text-lf-success-700' : 'text-lf-text-subtle',
        )}
      >
        {saving ? '…' : checked ? 'ON' : 'OFF'}
      </span>
    </button>
  );
}

function FlagRow({
  flag,
  saving,
  rowError,
  onToggle,
}: {
  flag: EffectiveFeatureFlag;
  saving: boolean;
  rowError: string | null;
  onToggle: () => void;
}) {
  return (
    <tr className="border-t border-lf-border transition-colors hover:bg-lf-canvas-subtle max-md:block max-md:rounded-lf-card max-md:border max-md:border-lf-border max-md:bg-white max-md:p-4">
      <td data-th="Flag" className={`px-4 py-3 ${MOBILE_TABLE_CELL}`}>
        <code className="rounded-lf-control bg-lf-canvas-subtle px-2 py-1 text-xs text-lf-text">
          {flag.key}
        </code>
      </td>
      <td data-th="Source" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>{flag.source}</td>
      <td data-th="State" className={`px-4 py-3 ${MOBILE_TABLE_CELL}`}>
        <div className="flex items-center gap-3">
          <ToggleSwitch
            checked={flag.is_enabled}
            saving={saving}
            disabled={false}
            onToggle={onToggle}
            label={`Toggle feature flag ${flag.key}`}
          />
          <span className="text-sm text-lf-text-muted">
            {saving ? 'Saving…' : flag.is_enabled ? 'Enabled' : 'Disabled'}
          </span>
        </div>
        {rowError && (
          <p role="alert" className="mt-1 text-xs text-lf-error-700">
            {rowError}
          </p>
        )}
      </td>
    </tr>
  );
}

/** Admin → Feature Flags: firm-level rollout toggles with per-row save state. */
export default function AdminFeatureFlagsPage(): React.JSX.Element {
  const [flags, setFlags] = useState<EffectiveFeatureFlag[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [savingKeys, setSavingKeys] = useState<Set<string>>(new Set());
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    adminApi
      .listFeatureFlags()
      .then((result) => setFlags([...result].sort((a, b) => a.key.localeCompare(b.key))))
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleToggle = (flag: EffectiveFeatureFlag) => {
    const key = flag.key;
    const nextValue = !flag.is_enabled;
    setSavingKeys((prev) => new Set(prev).add(key));
    setRowErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    adminApi
      .setFeatureFlag(key, nextValue)
      .then((updated) => {
        setFlags((prev) =>
          prev.map((f) => (f.key === key ? { ...f, is_enabled: updated.is_enabled } : f)),
        );
      })
      .catch((err: unknown) =>
        setRowErrors((prev) => ({
          ...prev,
          [key]:
            err instanceof ApiError && err.isPermissionDenied
              ? 'Changing flags requires the firm_admin role for your firm.'
              : err instanceof Error
                ? err.message
                : 'Could not update the flag.',
        })),
      )
      .finally(() =>
        setSavingKeys((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        }),
      );
  };

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Feature Flags</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Rollout controls for your firm — enabling or disabling a capability here affects
          your firm&apos;s users only.
        </p>
      </div>

      <FirmScopeNote>
        Flag changes apply to your firm. The source column shows where the effective value
        comes from.
      </FirmScopeNote>

      {loading && <LoadingState lines={5} label="Loading feature flags…" />}

      {error != null && !loading ? <AdminErrorState error={error} onRetry={load} /> : null}

      {!loading && !error && flags.length === 0 && (
        <EmptyState
          title="No feature flags"
          description="Your firm has no feature flags configured yet. Flags appear here when the platform registers new capabilities."
        />
      )}

      {!loading && !error && flags.length > 0 && (
        <Card>
          <div className="overflow-x-auto md:max-h-[75vh] md:overflow-y-auto">
            <table className="w-full text-left text-sm tabular-nums md:min-w-[560px]">
              <caption className="sr-only">Feature flags</caption>
              <thead className="max-md:hidden md:sticky md:top-0 md:bg-white">
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Flag
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Source
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    State
                  </th>
                </tr>
              </thead>
              <tbody className="max-md:block max-md:space-y-3 max-md:bg-lf-canvas-subtle max-md:p-4">
                {flags.map((flag) => (
                  <FlagRow
                    key={flag.key}
                    flag={flag}
                    saving={savingKeys.has(flag.key)}
                    rowError={rowErrors[flag.key] ?? null}
                    onToggle={() => handleToggle(flag)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
