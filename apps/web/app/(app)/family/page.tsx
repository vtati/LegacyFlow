'use client';

import Link from 'next/link';
import { useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { ProgressBar } from '@/components/family/progress-bar';
import { AssistantPanel } from '@/components/ai/assistant-panel';
import {
  MatterSelect,
  formatStatus,
  useLiveCoverage,
  useMatters,
} from '@/components/family/matter-context';
import { api, type MatterSummary } from '@/lib/api-client';
import { cn } from '@/lib/utils';

function statusTone(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized.includes('approv') || normalized.includes('complet')) {
    return 'bg-lf-success-100 text-lf-success-700';
  }
  if (normalized.includes('review') || normalized.includes('submit')) {
    return 'bg-lf-accent-100 text-lf-accent-800';
  }
  return 'bg-lf-primary-50 text-lf-primary-800';
}

function MatterCard({ matter, pct }: { matter: MatterSummary; pct: number }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="truncate">{matter.title}</CardTitle>
            <CardDescription>
              {matter.household?.name ?? 'Household'} · {matter.jurisdiction_code}
            </CardDescription>
          </div>
          <span
            className={cn(
              'shrink-0 rounded-full px-2.5 py-1 text-xs font-medium',
              statusTone(matter.status),
            )}
          >
            {formatStatus(matter.status)}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-3">
          <ProgressBar
            value={pct}
            label={`${matter.title} progress: ${Math.round(pct)}% complete`}
            className="flex-1"
          />
          <span className="shrink-0 text-xs font-medium text-lf-text-muted" aria-hidden="true">
            {Math.round(pct)}%
          </span>
        </div>
        <Link
          href={`/family/intake/${matter.id}`}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
        >
          Continue
          <ArrowRight className="size-4" aria-hidden="true" />
        </Link>
      </CardContent>
    </Card>
  );
}

/** Family dashboard — the family's matters with progress and continue links. */
export default function FamilyDashboardPage(): React.JSX.Element {
  const { matters, loading, error, reload } = useMatters();
  // Coverage reads the live intake-progress figure, not the stored
  // lifecycle proxy (see useLiveCoverage).
  const coverage = useLiveCoverage(matters);
  const [assistantMatterId, setAssistantMatterId] = useState<string | null>(null);

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Dashboard</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Your plans in progress. Your attorney will review everything before anything is finalized.
        </p>
      </div>

      {loading && <LoadingState lines={4} label="Loading your matters…" />}

      {error != null && !loading ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="When your firm starts a plan for your household, it will appear here. If you expected to see something, ask your firm to check your invitation."
        />
      )}

      {!loading && !error && matters.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {matters.map((matter) => (
            <MatterCard key={matter.id} matter={matter} pct={coverage(matter)} />
          ))}
        </div>
      )}

      {!loading && !error && matters.length > 0 && (
        <section aria-label="AI assistant" className="space-y-4 pt-2">
          <MatterSelect
            matters={matters}
            value={assistantMatterId ?? matters[0]?.id ?? null}
            onChange={setAssistantMatterId}
            id="assistant-matter-select"
          />
          <AssistantPanel
            panelId="dashboard-assistant"
            ask={(question) => api.askAi(assistantMatterId ?? matters[0]?.id ?? '', question)}
          />
        </section>
      )}
    </div>
  );
}
