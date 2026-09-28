'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Landmark, Scale, Wallet } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { useMatters } from '@/components/family/matter-context';
import { assetTypeLabel } from '@/components/family/asset-form';
import { liabilityTypeLabel } from '@/components/family/liability-form';
import {
  api,
  type Asset,
  type HouseholdRef,
  type Liability,
  type MatterSummary,
} from '@/lib/api-client';
import { formatMoney } from '@/lib/format';
import { cn } from '@/lib/utils';

interface HouseholdSnapshot {
  household: HouseholdRef;
  assets: Asset[];
  liabilities: Liability[];
}

interface TypeBreakdown {
  type: string;
  label: string;
  count: number;
  totalCents: number;
}

function breakdownByType(
  items: { asset_type?: string; liability_type?: string; estimated_value_cents?: number | null; balance_cents?: number }[],
  labelFor: (type: string) => string,
): TypeBreakdown[] {
  const map = new Map<string, TypeBreakdown>();
  for (const item of items) {
    const type = item.asset_type ?? item.liability_type ?? 'other';
    const value = item.asset_type != null ? (item.estimated_value_cents ?? 0) : (item.balance_cents ?? 0);
    const existing = map.get(type);
    if (existing) {
      existing.count += 1;
      existing.totalCents += value;
    } else {
      map.set(type, { type, label: labelFor(type), count: 1, totalCents: value });
    }
  }
  return [...map.values()].sort((a, b) => b.totalCents - a.totalCents);
}

function SummaryCard({
  label,
  valueCents,
  icon,
  tone,
}: {
  label: string;
  valueCents: number;
  icon: React.ReactNode;
  tone: 'asset' | 'liability' | 'net-positive' | 'net-negative';
}) {
  const tones: Record<string, string> = {
    asset: 'bg-lf-success-50 text-lf-success-700',
    liability: 'bg-lf-error-50 text-lf-error-700',
    'net-positive': 'bg-lf-primary-50 text-lf-primary-800',
    'net-negative': 'bg-lf-error-50 text-lf-error-700',
  };
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-5">
        <span
          className={cn(
            'flex size-11 shrink-0 items-center justify-center rounded-lf-control',
            tones[tone],
          )}
          aria-hidden="true"
        >
          {icon}
        </span>
        <div className="min-w-0">
          <p className="text-sm text-lf-text-muted">{label}</p>
          <p className="truncate text-xl font-bold text-lf-text">{formatMoney(valueCents)}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function HouseholdSection({ snapshot }: { snapshot: HouseholdSnapshot }) {
  const assetTotal = snapshot.assets.reduce(
    (sum, asset) => sum + (asset.estimated_value_cents ?? 0),
    0,
  );
  const liabilityTotal = snapshot.liabilities.reduce(
    (sum, liability) => sum + (liability.balance_cents ?? 0),
    0,
  );
  const net = assetTotal - liabilityTotal;
  const assetBreakdown = breakdownByType(snapshot.assets, assetTypeLabel);
  const liabilityBreakdown = breakdownByType(snapshot.liabilities, liabilityTypeLabel);

  return (
    <section aria-label={`${snapshot.household.name} finances`} className="space-y-4">
      <h2 className="text-lg font-semibold text-lf-text">{snapshot.household.name}</h2>

      <div className="grid gap-4 sm:grid-cols-3">
        <SummaryCard
          label="Total assets"
          valueCents={assetTotal}
          icon={<Wallet className="size-5" aria-hidden="true" />}
          tone="asset"
        />
        <SummaryCard
          label="Total liabilities"
          valueCents={liabilityTotal}
          icon={<Landmark className="size-5" aria-hidden="true" />}
          tone="liability"
        />
        <SummaryCard
          label="Net position"
          valueCents={net}
          icon={<Scale className="size-5" aria-hidden="true" />}
          tone={net >= 0 ? 'net-positive' : 'net-negative'}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-base">Assets by type</CardTitle>
              <Link
                href="/family/assets"
                className="inline-flex min-h-11 items-center gap-1 rounded-lf-control text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              >
                Edit <ArrowRight className="size-4" aria-hidden="true" />
              </Link>
            </div>
            <CardDescription>
              {snapshot.assets.length === 0
                ? 'No assets recorded yet.'
                : `${snapshot.assets.length} ${snapshot.assets.length === 1 ? 'asset' : 'assets'}`}
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {assetBreakdown.length === 0 ? (
              <p className="px-4 py-6 text-sm text-lf-text-muted">
                Add your home, accounts, vehicles, and other property in Assets.
              </p>
            ) : (
              <ul className="divide-y divide-lf-border">
                {assetBreakdown.map((row) => (
                  <li key={row.type} className="flex items-center justify-between gap-3 px-4 py-2.5">
                    <p className="text-sm text-lf-text">
                      {row.label}
                      <span className="ml-1.5 text-xs text-lf-text-subtle">× {row.count}</span>
                    </p>
                    <p className="text-sm font-medium text-lf-text">{formatMoney(row.totalCents)}</p>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-base">Liabilities by type</CardTitle>
              <Link
                href="/family/liabilities"
                className="inline-flex min-h-11 items-center gap-1 rounded-lf-control text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              >
                Edit <ArrowRight className="size-4" aria-hidden="true" />
              </Link>
            </div>
            <CardDescription>
              {snapshot.liabilities.length === 0
                ? 'No liabilities recorded yet.'
                : `${snapshot.liabilities.length} ${snapshot.liabilities.length === 1 ? 'liability' : 'liabilities'}`}
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {liabilityBreakdown.length === 0 ? (
              <p className="px-4 py-6 text-sm text-lf-text-muted">
                Add mortgages, loans, and other debts in Liabilities.
              </p>
            ) : (
              <ul className="divide-y divide-lf-border">
                {liabilityBreakdown.map((row) => (
                  <li key={row.type} className="flex items-center justify-between gap-3 px-4 py-2.5">
                    <p className="text-sm text-lf-text">
                      {row.label}
                      <span className="ml-1.5 text-xs text-lf-text-subtle">× {row.count}</span>
                    </p>
                    <p className="text-sm font-medium text-lf-text">{formatMoney(row.totalCents)}</p>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

/** Assets & Accounts — combined assets + liabilities with net position per household. */
export default function FamilyAssetsAccountsPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [snapshots, setSnapshots] = useState<HouseholdSnapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async (list: MatterSummary[]) => {
    const unique = new Map<string, HouseholdRef>();
    for (const matter of list) {
      if (!unique.has(matter.household.id)) unique.set(matter.household.id, matter.household);
    }
    setLoading(true);
    setError(null);
    try {
      const results = await Promise.all(
        [...unique.values()].map(async (household): Promise<HouseholdSnapshot | null> => {
          const [assetsResult, liabilitiesResult] = await Promise.allSettled([
            api.listAssets(household.id, 1, 100),
            api.listLiabilities(household.id, 1, 100),
          ]);
          if (assetsResult.status !== 'fulfilled' || liabilitiesResult.status !== 'fulfilled') {
            return null;
          }
          return {
            household,
            assets: assetsResult.value.items,
            liabilities: liabilitiesResult.value.items,
          };
        }),
      );
      setSnapshots(results.filter((s): s is HouseholdSnapshot => s !== null));
    } catch (err: unknown) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!mattersLoading && !mattersError && matters.length > 0) {
      void load(matters);
    }
  }, [matters, mattersLoading, mattersError, load]);

  const retry = useCallback(() => {
    if (mattersError) reloadMatters();
    else void load(matters);
  }, [mattersError, reloadMatters, load, matters]);

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Assets &amp; Accounts</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          What you own and what you owe, side by side. These figures are estimates from your
          entries — nothing here is final until your attorney approves it.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={retry} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your assets and liabilities will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && loading && (
        <LoadingState lines={5} label="Loading assets and liabilities…" />
      )}
      {error != null && !loading ? <ErrorState error={error} onRetry={retry} /> : null}

      {!loading && !error &&
        snapshots.map((snapshot) => (
          <HouseholdSection key={snapshot.household.id} snapshot={snapshot} />
        ))}
    </div>
  );
}
