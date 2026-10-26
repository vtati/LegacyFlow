'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, Info } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { ProgressBar } from '@/components/family/progress-bar';
import { QuestionField } from '@/components/family/intake/question-field';
import { formatStatus } from '@/components/family/matter-context';
import {
  ApiError,
  api,
  type AnswerMap,
  type EvaluateResponse,
  type IntakeProgressResponse,
  type MatterSummary,
  type Person,
  type QuestionSection,
  type QuestionSet,
} from '@/lib/api-client';
import { cn } from '@/lib/utils';

const AUTOSAVE_DELAY_MS = 800;

interface WizardSection {
  setId: string;
  setTitle: string;
  section: QuestionSection;
}

interface IntakeWizardProps {
  matterId: string;
}

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

function flattenSections(
  sets: { household: QuestionSet } & Record<string, QuestionSet>,
): WizardSection[] {
  return Object.values(sets).flatMap((set) =>
    set.sections.map((section) => ({
      setId: set.question_set.id,
      setTitle: set.question_set.title,
      section,
    })),
  );
}

export function IntakeWizard({ matterId }: IntakeWizardProps): React.JSX.Element {
  const [matter, setMatter] = useState<MatterSummary | null>(null);
  const [sections, setSections] = useState<WizardSection[]>([]);
  const [answers, setAnswers] = useState<AnswerMap>({});
  const [persons, setPersons] = useState<Person[]>([]);
  const [progress, setProgress] = useState<IntakeProgressResponse | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const [evaluation, setEvaluation] = useState<EvaluateResponse | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [missingItems, setMissingItems] = useState<unknown[]>([]);
  const [submitted, setSubmitted] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const headingRef = useRef<HTMLHeadingElement>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hydrated = useRef(false);
  const answersRef = useRef<AnswerMap>({});
  const evaluationRef = useRef<HTMLDivElement>(null);

  const reload = useCallback(() => {
    setLoading(true);
    setLoadError(null);
    Promise.all([
      api.getMatter(matterId),
      api.getQuestionSets(matterId),
      api.getAnswers(matterId),
      api.getHousehold(matterId),
      api.getIntakeProgress(matterId),
    ])
      .then(([matterDetail, questionSets, answersResponse, household, intakeProgress]) => {
        setMatter(matterDetail.matter);
        setSections(flattenSections(questionSets.question_sets));
        setAnswers(answersResponse.answers);
        answersRef.current = answersResponse.answers;
        setPersons(household.persons);
        setProgress(intakeProgress);
        hydrated.current = true;
      })
      .catch((err: unknown) => setLoadError(err))
      .finally(() => setLoading(false));
  }, [matterId]);

  useEffect(() => {
    reload();
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [reload]);

  // Debounced autosave — sends the full answers map; never includes PII in logs.
  useEffect(() => {
    if (!hydrated.current) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    setSaveState('saving');
    saveTimer.current = setTimeout(() => {
      api
        .putAnswers(matterId, answersRef.current)
        .then(() => {
          setSaveState('saved');
          return api.getIntakeProgress(matterId);
        })
        .then((intakeProgress) => setProgress(intakeProgress))
        .catch(() => setSaveState('error'));
    }, AUTOSAVE_DELAY_MS);
  }, [answers, matterId]);

  const handleAnswerChange = useCallback((key: string, value: AnswerMap[string]) => {
    const next = { ...answersRef.current, [key]: value };
    answersRef.current = next;
    setAnswers(next);
  }, []);

  const goToSection = useCallback((index: number) => {
    setActiveIndex(index);
    requestAnimationFrame(() => headingRef.current?.focus());
  }, []);

  const handleEvaluate = useCallback(() => {
    setEvaluating(true);
    api
      .evaluateIntake(matterId)
      .then((result) => {
        setEvaluation(result);
        requestAnimationFrame(() => evaluationRef.current?.focus());
      })
      .catch((err: unknown) => setSubmitError(err))
      .finally(() => setEvaluating(false));
  }, [matterId]);

  const handleSubmit = useCallback(() => {
    setSubmitting(true);
    setSubmitError(null);
    setMissingItems([]);
    api
      .submitMatter(matterId)
      .then((matterDetail) => {
        setMatter(matterDetail.matter);
        setSubmitted(true);
        window.scrollTo({ top: 0, behavior: 'smooth' });
      })
      .catch((err: unknown) => {
        setSubmitError(err);
        if (err instanceof ApiError && err.status === 409) {
          const body = err.details as { missing?: unknown } | null;
          const missing = body?.missing;
          setMissingItems(Array.isArray(missing) ? missing : []);
        }
        window.scrollTo({ top: 0, behavior: 'smooth' });
      })
      .finally(() => setSubmitting(false));
  }, [matterId]);

  const blockingRules = useMemo(
    () => (evaluation?.triggered ?? []).filter((rule) => rule.severity === 'blocking'),
    [evaluation],
  );
  const advisoryRules = useMemo(
    () => (evaluation?.triggered ?? []).filter((rule) => rule.severity !== 'blocking'),
    [evaluation],
  );

  const activeSection = sections[activeIndex];
  const overallProgress = progress?.overall;

  if (loading) {
    return <LoadingState lines={5} label="Loading your intake questions…" />;
  }

  if (loadError) {
    return <ErrorState error={loadError} onRetry={reload} />;
  }

  if (sections.length === 0) {
    return (
      <EmptyState
        variant="aurora"
        title="No questions available"
        description="Your firm's intake questions are not published yet. Please check back later or ask your firm."
        action={
          <Button variant="outline" onClick={reload}>
            Try again
          </Button>
        }
      />
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">
          Intake{matter ? `: ${matter.title}` : ''}
        </h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Answer at your own pace — everything saves automatically. Your attorney will review your
          answers; nothing here is legal advice.
        </p>
      </div>

      {matter && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-lf-text-muted">
          <span>
            {matter.household?.name ?? 'Household'} · {matter.jurisdiction_code}
          </span>
          <span className="rounded-full bg-lf-primary-50 px-2.5 py-1 text-xs font-medium text-lf-primary-800">
            {formatStatus(matter.status)}
          </span>
          <span role="status" className="ml-auto text-xs">
            {saveState === 'saving' && 'Saving…'}
            {saveState === 'saved' && 'All answers saved'}
            {saveState === 'error' && 'Save failed — check your connection'}
          </span>
        </div>
      )}

      {overallProgress && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium text-lf-text">Overall progress</span>
            <span className="text-lf-text-muted">
              {overallProgress.required_answered} of {overallProgress.required_total} required
              answered
            </span>
          </div>
          <ProgressBar value={overallProgress.percent_complete} label="Overall intake progress" />
        </div>
      )}

      {submitted && (
        <Card className="border-lf-success-600">
          <CardHeader>
            <div className="flex items-center gap-3">
              <CheckCircle2 className="size-6 text-lf-success-700" aria-hidden="true" />
              <div>
                <CardTitle>Submitted for attorney review</CardTitle>
                <CardDescription>
                  Thank you. Your answers are with your firm now — they will reach out with next
                  steps.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
        </Card>
      )}

      {submitError != null && !(submitError instanceof ApiError && submitError.status === 409) ? (
        <Card className="border-lf-error-600" role="alert">
          <CardContent className="pt-6">
            <p className="text-sm font-medium text-lf-text">Could not submit right now</p>
            <p className="mt-1 text-sm text-lf-text-muted">
              {submitError instanceof Error ? submitError.message : 'Please try again in a moment.'}
            </p>
          </CardContent>
        </Card>
      ) : null}

      {missingItems.length > 0 && (
        <Card className="border-lf-accent-600" role="alert">
          <CardHeader>
            <CardTitle>A few things are still needed</CardTitle>
            <CardDescription>
              Your firm needs these before the matter can be submitted:
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="list-disc space-y-1 pl-5 text-sm text-lf-text">
              {missingItems.map((item, index) => (
                <li key={index}>{String(item)}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
        <nav aria-label="Intake sections" className="lg:sticky lg:top-24 lg:self-start">
          <ol className="flex gap-2 overflow-x-auto lg:flex-col lg:overflow-visible">
            {sections.map((entry, index) => {
              const setProgress = progress?.sets[entry.setId];
              const isActive = index === activeIndex;
              return (
                <li key={entry.section.id} className="shrink-0 lg:shrink">
                  <button
                    type="button"
                    onClick={() => goToSection(index)}
                    aria-current={isActive ? 'step' : undefined}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-lf-control border px-3 py-2.5 text-left text-sm transition-colors',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500',
                      isActive
                        ? 'border-lf-primary-700 bg-lf-primary-50 font-semibold text-lf-primary-800'
                        : 'border-lf-border bg-lf-canvas-elevated text-lf-text-muted hover:bg-lf-canvas-subtle',
                    )}
                  >
                    <span
                      className={cn(
                        'flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold',
                        isActive
                          ? 'bg-lf-primary-700 text-lf-primary-foreground dark:text-lf-primary-950'
                          : 'bg-lf-canvas-subtle text-lf-text-muted',
                      )}
                      aria-hidden="true"
                    >
                      {index + 1}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{entry.section.title}</span>
                      {setProgress && (
                        <span
                          className={cn(
                            'block text-xs font-normal',
                            // Active step sits on bg-lf-primary-50 (#EEF2FF):
                            // text-lf-text-subtle (#64748B) is only 4.25:1
                            // there, below the 4.5:1 WCAG AA bar. Use the
                            // darker muted tone on the tinted background.
                            isActive ? 'text-lf-text-muted' : 'text-lf-text-subtle',
                          )}
                        >
                          {setProgress.percent_complete}% complete
                        </span>
                      )}
                      {setProgress && (
                        <span
                          className="mt-1.5 block h-1 w-full overflow-hidden rounded-full bg-lf-accent-100"
                          aria-hidden="true"
                        >
                          <span
                            className="lf-lumen-fill block h-full rounded-full"
                            style={{ width: `${setProgress.percent_complete}%` }}
                          />
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>

        <div className="space-y-6">
          {activeSection && (
            <Card key={activeSection.section.id} className="lf-gradient-ring shadow-lf-card">
              <CardHeader>
                <CardDescription>{activeSection.setTitle}</CardDescription>
                <h2
                  ref={headingRef}
                  tabIndex={-1}
                  className="text-xl font-semibold tracking-tight text-lf-text outline-none"
                >
                  {activeSection.section.title}
                </h2>
              </CardHeader>
              <CardContent className="space-y-6">
                {activeSection.section.questions.map((question) => (
                  <QuestionField
                    key={question.key}
                    question={question}
                    value={answers[question.key] ?? null}
                    onChange={(value) => handleAnswerChange(question.key, value)}
                    persons={persons}
                  />
                ))}
                <div className="flex items-center justify-between border-t border-lf-border pt-4">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => goToSection(activeIndex - 1)}
                    disabled={activeIndex === 0}
                  >
                    <ArrowLeft className="size-4" aria-hidden="true" />
                    Previous
                  </Button>
                  <span className="text-xs text-lf-text-subtle" aria-hidden="true">
                    Section {activeIndex + 1} of {sections.length}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => goToSection(activeIndex + 1)}
                    disabled={activeIndex === sections.length - 1}
                  >
                    Next
                    <ArrowRight className="size-4" aria-hidden="true" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Review and submit</CardTitle>
              <CardDescription>
                Check your answers for anything your attorney will want to look at, then send the
                matter for review.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-wrap gap-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleEvaluate}
                  disabled={evaluating || submitted}
                >
                  {evaluating ? 'Checking…' : 'Check my answers'}
                </Button>
                <Button type="button" onClick={handleSubmit} disabled={submitting || submitted}>
                  {submitting ? 'Submitting…' : 'Submit for attorney review'}
                </Button>
              </div>

              <div ref={evaluationRef} tabIndex={-1} className="space-y-4">
                {evaluation && blockingRules.length > 0 && (
                  <div
                    role="alert"
                    className="rounded-lf-card border border-lf-error-600 bg-lf-error-50 p-4"
                  >
                    <div className="flex items-start gap-3">
                      <AlertTriangle
                        className="mt-0.5 size-5 shrink-0 text-lf-error-700"
                        aria-hidden="true"
                      />
                      <div className="space-y-2">
                        <p className="font-semibold text-lf-error-700">Attorney review required</p>
                        <p className="text-sm text-lf-text">
                          Some of your answers need an attorney&apos;s attention before this plan
                          can move forward.
                        </p>
                        <ul className="space-y-2">
                          {blockingRules.map((rule) => (
                            <li key={rule.rule_id} className="text-sm text-lf-text">
                              <span className="font-medium">{rule.outcome}: </span>
                              {rule.user_message}
                            </li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  </div>
                )}

                {evaluation && (
                  <div className="space-y-2">
                    <p className="text-sm font-medium text-lf-text">
                      {advisoryRules.length > 0
                        ? 'Notes from your check'
                        : 'Your answers look complete — nothing else to review right now.'}
                    </p>
                    {advisoryRules.length > 0 && (
                      <ul className="space-y-2">
                        {advisoryRules.map((rule) => (
                          <li
                            key={rule.rule_id}
                            className="flex items-start gap-2.5 rounded-lf-control border border-lf-border bg-lf-canvas-subtle p-3 text-sm text-lf-text-muted"
                          >
                            <Info
                              className="mt-0.5 size-4 shrink-0 text-lf-primary-700"
                              aria-hidden="true"
                            />
                            <span>
                              <span className="font-medium text-lf-text">{rule.outcome}: </span>
                              {rule.user_message}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
