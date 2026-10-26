'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  ArrowRight,
  ClipboardCheck,
  ClipboardList,
  Compass,
  FileText,
  Files,
  Gauge,
  HelpCircle,
  Plus,
  Shield,
  Wallet,
  type LucideIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { CoverageRing } from '@/components/family/coverage-ring';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { CreateMatterModal } from '@/components/family/create-matter-modal';
import { useLiveCoverage, useMatters } from '@/components/family/matter-context';
import { ProgressBar } from '@/components/family/progress-bar';
import { StatusPill } from '@/components/family/status-pill';
import { cn } from '@/lib/utils';
import { type MatterSummary } from '@/lib/api-client';

type JewelTone = 'cyan' | 'sky' | 'violet' | 'mint' | 'amber' | 'navy';

/** Jewel icon-tile classes per tone (700-step icon on the 100 tint, AA-verified). */
const TONE_TILE: Record<JewelTone, string> = {
  cyan: 'bg-lf-accent-100 text-lf-accent-700',
  sky: 'bg-lf-sky-100 text-lf-sky-700',
  violet: 'bg-lf-violet-100 text-lf-violet-700',
  mint: 'bg-lf-success-100 text-lf-success-700',
  amber: 'bg-lf-warning-100 text-lf-warning-700',
  navy: 'bg-lf-primary-50 text-lf-primary-800',
};

interface QuickAction {
  href: string;
  label: string;
  blurb: string;
  icon: LucideIcon;
  tone: JewelTone;
}

const QUICK_ACTIONS: QuickAction[] = [
  {
    href: '/family/intake',
    label: 'Continue intake',
    blurb: 'Answer the questions for your plan.',
    icon: ClipboardList,
    tone: 'cyan',
  },
  {
    href: '/family/documents',
    label: 'Documents',
    blurb: 'Review your watermarked draft documents.',
    icon: FileText,
    tone: 'sky',
  },
  {
    href: '/family/assets',
    label: 'Assets',
    blurb: 'Add what you own and name beneficiaries.',
    icon: Wallet,
    tone: 'navy',
  },
  {
    href: '/family/guardians',
    label: 'Guardians',
    blurb: 'Choose guardians and trusted roles.',
    icon: Shield,
    tone: 'mint',
  },
  {
    href: '/family/checkup',
    label: 'Checkup',
    blurb: 'See everything waiting on you, per matter.',
    icon: ClipboardCheck,
    tone: 'amber',
  },
  {
    href: '/family/help',
    label: 'Help',
    blurb: 'How intake, review, and signing work.',
    icon: HelpCircle,
    tone: 'navy',
  },
];

function greetingForHour(hour: number): string {
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

interface NextStep {
  label: string;
  blurb: string;
  href: string;
  cta: string;
}

/**
 * The single most useful next action for a matter. Status is authoritative:
 * the API overwrites the matter's stored completeness_pct with a lifecycle
 * proxy on every status transition, so completeness only decides inside
 * the intake states — `pct` here is the live intake-progress figure.
 */
function nextStepFor(matter: MatterSummary, pct: number): NextStep {
  const status = matter.status.toLowerCase();
  const title = matter.title || 'Untitled matter';
  if (status.includes('clarification')) {
    return {
      label: 'Answer your firm\u2019s questions',
      blurb: `Your firm needs a few answers from you before ${title} can move forward.`,
      href: `/family/intake/${matter.id}`,
      cta: 'Answer questions',
    };
  }
  if (status.includes('escalat')) {
    return {
      label: 'Your firm is taking a closer look',
      blurb: 'Your firm flagged something in your plan for review — they will reach out if they need anything from you.',
      href: '/family/my-plan',
      cta: 'View my plan',
    };
  }
  if (status.includes('signing') || status.includes('funding')) {
    return {
      label: 'Complete your signing checklist',
      blurb: 'Your plan is ready to sign — work through the checklist to make it official.',
      href: '/family/signing',
      cta: 'Go to signing',
    };
  }
  if (status.includes('releas')) {
    return {
      label: 'Review your released documents',
      blurb: 'Your documents are ready — review them and finish your signing steps.',
      href: '/family/documents',
      cta: 'View documents',
    };
  }
  if (status.includes('review') || status.includes('submit') || status.includes('assembly')) {
    return {
      label: 'Your firm is preparing your documents',
      blurb: 'Your firm has your answers — take a look at the drafts taking shape.',
      href: '/family/documents',
      cta: 'View documents',
    };
  }
  if (status.includes('implement')) {
    return {
      label: 'Check your plan health',
      blurb: 'Your plan is in place — a quick review keeps it current as life changes.',
      href: '/family/checkup',
      cta: 'Run a checkup',
    };
  }
  if (status.includes('archiv')) {
    return {
      label: 'Review your plan',
      blurb: 'This plan is archived — open it to review what was put in place.',
      href: '/family/my-plan',
      cta: 'View my plan',
    };
  }
  if (pct < 100) {
    return {
      label: 'Finish your intake questions',
      blurb: `${title} is ${pct}% complete — your answers shape every document in your plan.`,
      href: `/family/intake/${matter.id}`,
      cta: 'Continue intake',
    };
  }
  return {
    label: 'Review and submit your intake',
    blurb: 'Your answers are complete — review them and send your plan to your firm.',
    href: `/family/intake/${matter.id}`,
    cta: 'Review intake',
  };
}

function MatterCard({ matter, pct }: { matter: MatterSummary; pct: number }) {
  return (
    <Card>
      <CardContent className="space-y-5 p-6">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <Link
              href="/family/my-plan"
              className="text-base font-semibold text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
            >
              {matter.title || 'Untitled matter'}
            </Link>
            <p className="mt-0.5 text-sm text-lf-text-muted">
              {matter.household?.name ?? 'Household'} · {matter.jurisdiction_code}
            </p>
          </div>
          <StatusPill status={matter.status} />
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-sm">
            <span className="text-lf-text-muted">Intake completeness</span>
            <span className="font-medium text-lf-text">{pct}%</span>
          </div>
          <ProgressBar
            value={pct}
            label={`${matter.title || 'Untitled matter'} intake progress`}
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href={`/family/intake/${matter.id}`}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-4 py-2 text-sm font-medium text-lf-text shadow-lf-subtle hover:bg-lf-canvas-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            Continue intake <ArrowRight className="size-4" aria-hidden="true" />
          </Link>
          <Link
            href={`/family/interview/${matter.id}`}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-4 py-2 text-sm font-medium text-lf-text shadow-lf-subtle hover:bg-lf-canvas-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            AI interview <ArrowRight className="size-4" aria-hidden="true" />
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

/** Family home — aurora welcome band with the coverage ring as hero, bento stats, matters, quick actions. */
export default function FamilyHomePage(): React.JSX.Element {
  const { matters, loading, error, reload } = useMatters();
  const [modalOpen, setModalOpen] = useState(false);
  const greeting = greetingForHour(new Date().getHours());
  const householdName = matters[0]?.household?.name ?? 'your household';
  const householdId = matters[0]?.household?.id ?? null;
  const primaryMatter = matters[0] ?? null;
  // Every coverage number on this page — the ring, the band line, the
  // next step, the average, and each matter card — resolves through the
  // live intake-progress figure for that matter. The stored
  // completeness_pct is a lifecycle proxy (the API overwrites it on
  // status transitions) and showed 9% on a fresh matter whose live
  // progress was 0%, next to a ring reading the live 0%.
  const coverage = useLiveCoverage(matters);
  const ringPct = primaryMatter ? coverage(primaryMatter) : 0;
  const primaryTitle = primaryMatter?.title || 'Untitled matter';
  const nextStep = primaryMatter ? nextStepFor(primaryMatter, ringPct) : null;
  const averageCoverage =
    matters.length > 0
      ? Math.round(matters.reduce((sum, m) => sum + coverage(m), 0) / matters.length)
      : 0;

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <section
        aria-label="Welcome"
        className="relative overflow-hidden rounded-lf-hero border border-lf-border bg-lf-panel-cyan shadow-lf-card"
      >
        <div aria-hidden="true" className="absolute inset-0 bg-lf-aurora" />
        <div className="relative flex flex-col gap-6 p-6 sm:p-8 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">
              {greeting}, {householdName}
            </h1>
            <p className="mt-2 max-w-md text-[15px] leading-relaxed text-lf-text-muted">
              Your estate plan at a glance — pick up where you left off, or see what needs your
              attention.
            </p>
            {primaryMatter && (
              <p className="mt-4 text-sm text-lf-text-muted">
                <span className="font-medium text-lf-text">{primaryTitle}</span> ·{' '}
                {primaryMatter.jurisdiction_code} · {ringPct}% of intake complete
              </p>
            )}
            {!loading && !error && matters.length === 0 && (
              <div className="mt-5">
                <Button
                  onClick={() => setModalOpen(true)}
                  className="lf-pressable bg-lf-gradient-action text-white shadow-lf-glow hover:opacity-95"
                >
                  <Plus className="size-4" aria-hidden="true" />
                  Start new estate plan
                </Button>
              </div>
            )}
          </div>
          {primaryMatter && (
            <div className="flex shrink-0 flex-col items-center gap-2 self-center">
              <CoverageRing value={ringPct} label={`${primaryTitle} coverage`} />
              <p className="max-w-40 text-center text-xs text-lf-text-muted">
                {primaryTitle} coverage
              </p>
            </div>
          )}
        </div>
      </section>

      {loading && <LoadingState lines={4} label="Loading your home…" />}

      {error != null && !loading ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error && matters.length === 0 && (
        <EmptyState
          variant="aurora"
          title="No matters yet"
          description="Start your first estate plan — answer a few questions and our AI intake assistant will guide you through the rest."
        />
      )}

      {!loading && !error && matters.length > 0 && nextStep && (
        <section aria-label="Your next step and stats" className="grid gap-4 lg:grid-cols-4">
          <Card className="border-lf-accent-200 bg-lf-panel-cyan lg:col-span-2">
            <CardContent className="flex h-full flex-col gap-4 p-6 sm:flex-row sm:items-center">
              <div className="flex min-w-0 flex-1 items-start gap-4 sm:items-center">
                <span
                  className={cn(
                    'flex size-11 shrink-0 items-center justify-center rounded-lf-control',
                    TONE_TILE.cyan,
                  )}
                >
                  <Compass className="size-5" aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-semibold uppercase tracking-wider text-lf-accent-700">
                    Your next step
                  </p>
                  <p className="mt-1 text-base font-semibold text-lf-text">{nextStep.label}</p>
                  <p className="mt-0.5 text-sm text-lf-text-muted">{nextStep.blurb}</p>
                </div>
              </div>
              <Link
                href={nextStep.href}
                className="lf-pressable inline-flex min-h-11 shrink-0 items-center gap-1.5 self-start rounded-lf-control bg-lf-gradient-action px-4 py-2 text-sm font-medium text-white shadow-lf-glow hover:opacity-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-accent-600 focus-visible:ring-offset-2 sm:self-center"
              >
                {nextStep.cta} <ArrowRight className="size-4" aria-hidden="true" />
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 p-6">
              <span
                className={cn(
                  'flex size-11 items-center justify-center rounded-lf-control',
                  TONE_TILE.sky,
                )}
              >
                <Files className="size-5" aria-hidden="true" />
              </span>
              <div>
                <p className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">
                  {matters.length}
                </p>
                <p className="mt-0.5 text-sm text-lf-text-muted">
                  {matters.length === 1 ? 'estate plan' : 'estate plans'} for your household
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 p-6">
              <span
                className={cn(
                  'flex size-11 items-center justify-center rounded-lf-control',
                  TONE_TILE.cyan,
                )}
              >
                <Gauge className="size-5" aria-hidden="true" />
              </span>
              <div>
                <p className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">
                  {averageCoverage}%
                </p>
                <p className="mt-0.5 text-sm text-lf-text-muted">average intake coverage</p>
              </div>
              <ProgressBar value={averageCoverage} label="Average intake coverage" />
            </CardContent>
          </Card>
        </section>
      )}

      {!loading && !error && matters.length > 0 && (
        <section aria-label="Your matters" className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-lf-text">Your matters</h2>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setModalOpen(true)}
              className="lf-pressable"
            >
              <Plus className="size-4" aria-hidden="true" />
              New estate plan
            </Button>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {matters.map((matter) => (
              <MatterCard key={matter.id} matter={matter} pct={coverage(matter)} />
            ))}
          </div>
        </section>
      )}

      {!loading && !error && (
        <section aria-label="Quick actions" className="space-y-4">
          <h2 className="text-lg font-semibold text-lf-text">Quick actions</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {QUICK_ACTIONS.map((action) => (
              <Card key={action.href} className="transition-colors hover:border-lf-border-strong">
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-3">
                    <span
                      className={cn(
                        'flex size-10 shrink-0 items-center justify-center rounded-lf-control',
                        TONE_TILE[action.tone],
                      )}
                    >
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
      <CreateMatterModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        householdId={householdId}
        householdName={matters[0]?.household?.name ?? null}
        onCreated={reload}
      />
    </div>
  );
}
