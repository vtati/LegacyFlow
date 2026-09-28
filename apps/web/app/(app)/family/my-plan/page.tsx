'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, CheckCircle2, CircleAlert } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { useMatters } from '@/components/family/matter-context';
import { ProgressBar } from '@/components/family/progress-bar';
import {
  api,
  type AnswerMap,
  type IntakeProgressResponse,
  type MatterSummary,
  type Nomination,
  type Person,
  type Question,
  type QuestionSetsResponse,
} from '@/lib/api-client';
import { formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

const NOMINATION_TYPES = ['guardian', 'executor', 'trustee'] as const;

const NOMINATION_LABELS: Record<string, string> = {
  guardian: 'Guardian',
  executor: 'Executor',
  trustee: 'Trustee',
};

interface Decision {
  label: string;
  value: string;
}

interface NextStep {
  label: string;
  href: string;
}

interface MatterPlan {
  matter: MatterSummary;
  progress: IntakeProgressResponse | null;
  decisions: Decision[];
  nominations: Record<string, Nomination[]>;
  persons: Person[];
  nextSteps: NextStep[];
}

function personName(persons: Person[], personId: string): string {
  return persons.find((person) => person.id === personId)?.full_name ?? 'Unnamed person';
}

function answerDisplay(question: Question, value: NonNullable<AnswerMap[string]>): string {
  if (question.type === 'boolean') {
    return value === true || value === 'true' ? 'Yes' : 'No';
  }
  if (question.type === 'single_select') {
    const option = question.options?.find((opt) => opt.value === value);
    return option?.label ?? String(value);
  }
  if (Array.isArray(value)) return value.join(', ');
  return String(value);
}

/** Pull the answered single-select/boolean questions out as "your choices". */
function extractDecisions(
  questionSets: QuestionSetsResponse | null,
  answers: AnswerMap,
): Decision[] {
  if (!questionSets) return [];
  const decisions: Decision[] = [];
  const sets = [questionSets.question_sets.household, questionSets.question_sets.guardians, questionSets.question_sets.assets];
  for (const set of sets) {
    for (const section of set.sections) {
      for (const question of section.questions) {
        if (question.type !== 'single_select' && question.type !== 'boolean') continue;
        const value = answers[question.key];
        if (value == null || value === '') continue;
        decisions.push({ label: question.label, value: answerDisplay(question, value) });
      }
    }
  }
  return decisions.slice(0, 10);
}

function buildNextSteps(
  matter: MatterSummary,
  nominations: Record<string, Nomination[]>,
  intakeComplete: boolean,
): NextStep[] {
  const steps: NextStep[] = [];
  const status = matter.status.toLowerCase();
  if (!intakeComplete) {
    steps.push({ label: 'Finish your intake questions', href: `/family/intake/${matter.id}` });
  }
  for (const type of NOMINATION_TYPES) {
    if ((nominations[type] ?? []).length === 0) {
      steps.push({
        label: `Name ${type === 'guardian' ? 'a guardian for your children' : `an ${NOMINATION_LABELS[type]?.toLowerCase() ?? type}`}`,
        href: '/family/guardians',
      });
    }
  }
  if (status.includes('review')) {
    steps.push({ label: 'Your firm is reviewing your plan — see documents', href: '/family/documents' });
  }
  if (status.includes('signing')) {
    steps.push({ label: 'Complete your signing checklist', href: '/family/signing' });
  }
  if (status.includes('funding')) {
    steps.push({ label: 'Work through your funding tasks', href: '/family/funding' });
  }
  if (status.includes('implemented')) {
    steps.push({ label: 'Schedule your annual checkup', href: '/family/checkup' });
  }
  if (steps.length === 0) {
    steps.push({ label: 'Review your plan health', href: '/family/checkup' });
  }
  return steps;
}

function MatterPlanCard({ plan }: { plan: MatterPlan }) {
  const { matter } = plan;
  const required = plan.progress?.overall;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle>{matter.title}</CardTitle>
            <CardDescription>
              {matter.household.name} · {matter.jurisdiction_code}
            </CardDescription>
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
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium text-lf-text">Plan completeness</span>
            <span className="font-medium text-lf-text">{matter.completeness_pct}%</span>
          </div>
          <ProgressBar value={matter.completeness_pct} label={`${matter.title} plan progress`} />
          {required && required.required_total > 0 && (
            <p className="text-xs text-lf-text-subtle">
              {required.required_answered} of {required.required_total} required intake answers
              complete
            </p>
          )}
        </div>

        <section aria-label={`${matter.title} nominations`}>
          <h3 className="text-sm font-semibold text-lf-text">Key roles</h3>
          <dl className="mt-2 divide-y divide-lf-border rounded-lf-control border border-lf-border">
            {NOMINATION_TYPES.map((type) => {
              const list = (plan.nominations[type] ?? []).slice().sort(
                (a, b) => a.priority - b.priority,
              );
              const first = list[0];
              return (
                <div key={type} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <dt className="text-sm text-lf-text-muted">{NOMINATION_LABELS[type]}</dt>
                  <dd className="text-sm font-medium text-lf-text">
                    {first ? (
                      <>
                        {personName(plan.persons, first.person_id)}
                        {list.length > 1 && (
                          <span className="ml-1.5 font-normal text-lf-text-subtle">
                            (+{list.length - 1} backup{list.length - 1 === 1 ? '' : 's'})
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="font-normal text-lf-accent-800">Not named yet</span>
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
        </section>

        {plan.decisions.length > 0 && (
          <section aria-label={`${matter.title} choices`}>
            <h3 className="text-sm font-semibold text-lf-text">Your choices so far</h3>
            <dl className="mt-2 space-y-1.5">
              {plan.decisions.map((decision) => (
                <div key={decision.label} className="flex items-start justify-between gap-3 text-sm">
                  <dt className="text-lf-text-muted">{decision.label}</dt>
                  <dd className="shrink-0 font-medium text-lf-text">{decision.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        <section aria-label={`${matter.title} next steps`}>
          <h3 className="text-sm font-semibold text-lf-text">Next steps</h3>
          <ul className="mt-2 space-y-1.5">
            {plan.nextSteps.map((step) => (
              <li key={step.label}>
                <Link
                  href={step.href}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-lf-control text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
                >
                  <ArrowRight className="size-4 shrink-0" aria-hidden="true" />
                  {step.label}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </CardContent>
    </Card>
  );
}

/** My Plan — per-matter overview: status, progress, roles, choices, next steps. */
export default function FamilyMyPlanPage(): React.JSX.Element {
  const { matters, loading: mattersLoading, error: mattersError, reload: reloadMatters } =
    useMatters();
  const [plans, setPlans] = useState<MatterPlan[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const loadPlans = useCallback(async (list: MatterSummary[]) => {
    setLoading(true);
    setError(null);
    try {
      const results = await Promise.all(
        list.map(async (matter): Promise<MatterPlan | null> => {
          const settled = await Promise.allSettled([
            api.getIntakeProgress(matter.id),
            api.getAnswers(matter.id),
            api.getQuestionSets(matter.id),
            api.getHousehold(matter.id),
            ...NOMINATION_TYPES.map((type) => api.listNominations(matter.id, type)),
          ]);
          // A matter is only skipped when its household (needed for names) fails.
          const householdResult = settled[3];
          if (householdResult.status !== 'fulfilled') return null;
          const progress =
            settled[0].status === 'fulfilled' ? settled[0].value : null;
          const answers: AnswerMap =
            settled[1].status === 'fulfilled' ? settled[1].value.answers : {};
          const questionSets =
            settled[2].status === 'fulfilled' ? settled[2].value : null;
          const nominations: Record<string, Nomination[]> = {};
          NOMINATION_TYPES.forEach((type, index) => {
            const result = settled[4 + index];
            nominations[type] =
              result?.status === 'fulfilled' && Array.isArray(result.value) ? result.value : [];
          });
          const matterPlan: MatterPlan = {
            matter,
            progress,
            decisions: extractDecisions(questionSets, answers),
            nominations,
            persons: householdResult.value.persons,
            nextSteps: [],
          };
          const required = progress?.overall;
          const intakeComplete =
            required != null && required.required_total > 0
              ? required.required_answered >= required.required_total
              : matter.completeness_pct >= 100;
          matterPlan.nextSteps = buildNextSteps(matter, nominations, intakeComplete);
          return matterPlan;
        }),
      );
      setPlans(results.filter((plan): plan is MatterPlan => plan !== null));
    } catch (err: unknown) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!mattersLoading && !mattersError && matters.length > 0) {
      void loadPlans(matters);
    }
  }, [matters, mattersLoading, mattersError, loadPlans]);

  const retry = useCallback(() => {
    if (mattersError) reloadMatters();
    else void loadPlans(matters);
  }, [mattersError, reloadMatters, loadPlans, matters]);

  const summary = useMemo(() => {
    const unnamed = plans.filter((plan) =>
      NOMINATION_TYPES.some((type) => (plan.nominations[type] ?? []).length === 0),
    ).length;
    return { unnamed };
  }, [plans]);

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">My Plan</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Where each of your plans stands, who fills the key roles, and what to do next. Nothing
          here is final until your attorney approves it.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={4} label="Loading your plan…" />}

      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={retry} />
      ) : null}

      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your plan overview will appear here once your firm starts a matter for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && loading && (
        <LoadingState lines={5} label="Loading plan details…" />
      )}

      {error != null && !loading ? <ErrorState error={error} onRetry={retry} /> : null}

      {!loading && !error && plans.length > 0 && summary.unnamed > 0 && (
        <p
          role="status"
          className="flex items-start gap-2 rounded-lf-control border border-lf-accent-600 bg-lf-accent-50 px-4 py-3 text-sm text-lf-accent-800"
        >
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {summary.unnamed === 1
            ? 'One of your matters is still missing a key role — name your guardian, executor, and trustee in Guardians.'
            : `${summary.unnamed} of your matters are still missing a key role — name your guardians, executors, and trustees in Guardians.`}
        </p>
      )}

      {!loading && !error && plans.length > 0 && summary.unnamed === 0 && (
        <p
          role="status"
          className="flex items-start gap-2 rounded-lf-control border border-lf-success-600 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
        >
          <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          Every matter has a guardian, executor, and trustee named. Nice work.
        </p>
      )}

      {!loading && !error && plans.length > 0 && (
        <div className="space-y-6">
          {plans.map((plan) => (
            <MatterPlanCard key={plan.matter.id} plan={plan} />
          ))}
        </div>
      )}
    </div>
  );
}
