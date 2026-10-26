'use client';

import Link from 'next/link';
import { ArrowRight } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { ProgressBar } from '@/components/family/progress-bar';
import {
  formatStatus,
  useLiveCoverage,
  useMatters,
} from '@/components/family/matter-context';

/** Intake matter picker — choose which matter's questions to answer. */
export default function FamilyIntakeIndexPage(): React.JSX.Element {
  const { matters, loading, error, reload } = useMatters();
  // Coverage reads the live intake-progress figure, not the stored
  // lifecycle proxy (see useLiveCoverage).
  const coverage = useLiveCoverage(matters);

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Intake</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          These questions help your attorney prepare your plan. Answers save automatically, and
          nothing here is legal advice.
        </p>
      </div>

      {loading && <LoadingState lines={4} label="Loading your matters…" />}

      {error != null && !loading ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error && matters.length === 0 && (
        <EmptyState
          variant="aurora"
          title="No matters yet"
          description="When your firm starts a plan for your household, its intake questions will appear here."
        />
      )}

      {!loading && !error && matters.length > 0 && (
        <div className="space-y-4">
          {matters.map((matter) => (
            <Card key={matter.id}>
              <CardContent className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0 flex-1 space-y-2">
                  <div>
                    <p className="font-semibold text-lf-text">{matter.title}</p>
                    <p className="text-sm text-lf-text-muted">
                      {matter.household?.name ?? 'Household'} · {matter.jurisdiction_code} ·{' '}
                      {formatStatus(matter.status)}
                    </p>
                  </div>
                  <ProgressBar
                    value={coverage(matter)}
                    label={`${matter.title} intake progress`}
                  />
                </div>
                <Button asChild className="shrink-0">
                  <Link href={`/family/intake/${matter.id}`}>
                    {coverage(matter) > 0 ? 'Continue intake' : 'Start intake'}
                    <ArrowRight className="size-4" aria-hidden="true" />
                  </Link>
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
