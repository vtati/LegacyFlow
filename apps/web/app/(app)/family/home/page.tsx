'use client';

import Link from 'next/link';
import {
  ArrowRight,
  ClipboardCheck,
  ClipboardList,
  FileText,
  HelpCircle,
  Shield,
  Wallet,
  type LucideIcon,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { useMatters } from '@/components/family/matter-context';
import { ProgressBar } from '@/components/family/progress-bar';
import { formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { MatterSummary } from '@/lib/api-client';

interface QuickAction {
  href: string;
  label: string;
  blurb: string;
  icon: LucideIcon;
}

const QUICK_ACTIONS: QuickAction[] = [
  {
    href: '/family/intake',
    label: 'Continue intake',
    blurb: 'Answer the questions for your plan.',
    icon: ClipboardList,
  },
  {
    href: '/family/documents',
    label: 'Documents',
    blurb: 'Review your watermarked draft documents.',
    icon: FileText,
  },
  {
    href: '/family/assets',
    label: 'Assets',
    blurb: 'Add what you own and name beneficiaries.',
    icon: Wallet,
  },
  {
    href: '/family/guardians',
    label: 'Guardians',
    blurb: 'Choose guardians and trusted roles.',
    icon: Shield,
  },
  {
    href: '/family/checkup',
    label: 'Checkup',
    blurb: 'See everything waiting on you, per matter.',
    icon: ClipboardCheck,
  },
  {
    href: '/family/help',
    label: 'Help',
    blurb: 'How intake, review, and signing work.',
    icon: HelpCircle,
  },
];

function greetingForHour(hour: number): string {
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

function MatterCard({ matter }: { matter: MatterSummary }) {
  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <Link
              href="/family/my-plan"
              className="text-base font-semibold text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
            >
              {matter.title || 'Untitled matter'}
            </Link>
            <p className="mt-0.5 text-sm text-lf-text-muted">
              {matter.household.name} · {matter.jurisdiction_code}
            </p>
          </div>
          <span
            className={cn(
              'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
              statusBadgeClass(matter.status),
            )}
          >
            {formatStatus(matter.status)}
          </span>
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-sm">
            <span className="text-lf-text-muted">Intake completeness</span>
            <span className="font-medium text-lf-text">{matter.completeness_pct}%</span>
          </div>
          <ProgressBar value={matter.completeness_pct} label={`${matter.title} intake progress`} />
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href={`/family/intake/${matter.id}`}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-4 py-2 text-sm font-medium text-lf-text shadow-lf-subtle hover:bg-lf-canvas-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            Continue intake <ArrowRight className="size-4" aria-hidden="true" />
          </Link>
          <Link
            href="/family/my-plan"
            className="inline-flex min-h-11 items-center rounded-lf-control px-4 py-2 text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            Plan details
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

/** Family home — greeting, matters at a glance, and quick actions. */
export default function FamilyHomePage(): React.JSX.Element {
  const { matters, loading, error, reload } = useMatters();
  const greeting = greetingForHour(new Date().getHours());
  const householdName = matters[0]?.household.name ?? 'your household';

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">
          {greeting}, {householdName}
        </h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Your estate plan at a glance — pick up where you left off, or see what needs your
          attention.
        </p>
      </div>

      {loading && <LoadingState lines={4} label="Loading your home…" />}

      {error != null && !loading ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="When your firm starts an estate plan for your household, it will appear here with its intake questions, documents, and next steps."
        />
      )}

      {!loading && !error && matters.length > 0 && (
        <section aria-label="Your matters" className="space-y-4">
          <h2 className="text-lg font-semibold text-lf-text">Your matters</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {matters.map((matter) => (
              <MatterCard key={matter.id} matter={matter} />
            ))}
          </div>
        </section>
      )}

      {!loading && !error && (
        <section aria-label="Quick actions" className="space-y-4">
          <h2 className="text-lg font-semibold text-lf-text">Quick actions</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {QUICK_ACTIONS.map((action) => (
              <Card key={action.href}>
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-3">
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
                      <action.icon className="size-5" aria-hidden="true" />
                    </span>
                    <CardTitle className="text-base">{action.label}</CardTitle>
                  </div>
                </CardHeader>
                <CardContent className="pt-0">
                  <CardDescription>{action.blurb}</CardDescription>
                  <Link
                    href={action.href}
                    aria-label={action.label}
                    className="mt-3 inline-flex min-h-11 items-center gap-1.5 text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
                  >
                    Open <ArrowRight className="size-4" aria-hidden="true" />
                  </Link>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
