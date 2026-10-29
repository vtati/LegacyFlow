'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, ArrowLeft, ArrowRight, Check, CheckCircle2, Info } from 'lucide-react';
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
  type MatterDetail,
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

/**
 * The summary embedded in the detail response can carry the generic
 * "Household" placeholder name — its serializer only sees a joined
 * household name when the LIST query produced the row. The same
 * response's top-level household is built from the household row
 * itself and always carries the real display name. Prefer it.
 */
function withDetailHousehold(detail: MatterDetail): MatterSummary {
  const top = detail.household;
  return {
    ...detail.matter,
    household: top?.name ? top : detail.matter.household,
  };
}

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

/** Smooth scrolling unless the user prefers reduced motion. */
function motionBehavior(): ScrollBehavior {
  if (typeof window === 'undefined') return 'auto';
  return window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 'auto' : 'smooth';
}

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
  const stripRef = useRef<HTMLOListElement>(null);
  const cardTopRef = useRef<HTMLDivElement>(null);
  const [fadeLeft, setFadeLeft] = useState(false);
  const [fadeRight, setFadeRight] = useState(false);
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
        setMatter(withDetailHousehold(matterDetail));
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
    requestAnimationFrame(() => {
      // Move focus for assistive tech without letting the browser's
      // focus-scroll fight the explicit card scroll below.
      headingRef.current?.focus({ preventScroll: true });
      // Land the question card right below the app header + sticky
      // tab strip (the wrapper carries the matching scroll-margin).
      cardTopRef.current?.scrollIntoView?.({ block: 'start', behavior: motionBehavior() });
    });
  }, []);

  // Edge fades on the mobile tab strip: a trailing/leading mask while
  // more chips sit off that edge — the scroll affordance for a strip
  // whose scrollbar is hidden.
  const updateFades = useCallback(() => {
    const strip = stripRef.current;
    if (!strip) return;
    setFadeLeft(strip.scrollLeft > 4);
    setFadeRight(strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 4);
  }, []);

  // Keep the active chip visible: scroll the STRIP ITSELF (never the
  // page) so the active chip lands centered. The <ol> is positioned,
  // so a chip's offsetLeft is its content coordinate inside the strip.
  // No horizontal overflow (desktop sidebar, or few sections) → no-op.
  const centerActiveChip = useCallback(() => {
    const strip = stripRef.current;
    if (!strip || strip.scrollWidth <= strip.clientWidth) return;
    const chip = strip.querySelectorAll('button')[activeIndex];
    if (!chip) return;
    const target = chip.offsetLeft - (strip.clientWidth - chip.offsetWidth) / 2;
    const max = Math.max(strip.scrollWidth - strip.clientWidth, 0);
    strip.scrollTo?.({
      left: Math.min(Math.max(target, 0), max),
      behavior: motionBehavior(),
    });
  }, [activeIndex]);

  // Signature of everything that can change a chip's WIDTH: each
  // section's floored percent plus its complete flag (which swaps
  // the figure for a check icon). Autosave replaces the progress
  // OBJECT after every save, even when no percent moved — depending
  // on the object would re-center (and visibly yank) the strip after
  // every typing pause. The signature re-centers only when a chip
  // label actually changed.
  const progressSignature = useMemo(
    () =>
      sections
        .map((entry) => {
          const p =
            progress?.sections?.[entry.setId]?.[entry.section.id] ??
            progress?.sets[entry.setId];
          if (!p) return '-';
          return `${Math.floor(p.percent_complete)}${p.percent_complete >= 100 ? '✓' : ''}`;
        })
        .join('|'),
    [sections, progress],
  );

  useEffect(() => {
    centerActiveChip();
    updateFades();
    // Smooth strip scrolling settles after the fade check above.
    const settle = setTimeout(updateFades, 400);
    return () => clearTimeout(settle);
  }, [centerActiveChip, updateFades, sections.length, progressSignature]);

  useEffect(() => {
    const onResize = () => {
      centerActiveChip();
      updateFades();
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [centerActiveChip, updateFades]);

  const stripMask =
    fadeLeft || fadeRight
      ? `linear-gradient(to right, ${fadeLeft ? 'transparent 0, black 20px' : 'black 0'}, ${
          fadeRight ? 'black calc(100% - 20px), transparent 100%' : 'black 100%'
        })`
      : undefined;

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
      .then((updated) => {
        // The submit endpoint returns the FLAT matter (no detail
        // envelope), serialized without the household/jurisdiction
        // joins — its embedded household is the "Household" placeholder
        // and its jurisdiction_code is null. Keep the household and
        // jurisdiction already on screen; take everything else (above
        // all the new status) from the response.
        setMatter((prev) => ({
          ...updated,
          household: prev?.household ?? updated.household,
          jurisdiction_code: updated.jurisdiction_code ?? prev?.jurisdiction_code ?? '',
        }));
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

      {/* Section navigation. Below lg this is a sticky horizontal tab
          strip (the standard pattern for long multi-section forms):
          chips scroll sideways INSIDE the strip only. The wrapper is a
          flex column there — a sticky element cannot travel inside a
          grid row the height of itself — and becomes the two-pane grid
          at lg, where the same list renders as the vertical sidebar.
          Overflow stays impossible by construction: min-w-0 panes, and
          the strip is a scroll container, so chip min-content can
          never widen the page (the pre-strip bug inflated the single
          mobile column to ~3,900px and pushed the card off-screen). */}
      <div className="flex flex-col gap-6 lg:grid lg:grid-cols-[260px_minmax(0,1fr)]">
        <nav
          aria-label="Intake sections"
          className="sticky top-16 z-30 -mx-4 min-w-0 border-b border-lf-border bg-lf-canvas px-4 py-2 lg:mx-0 lg:top-24 lg:z-auto lg:self-start lg:border-b-0 lg:bg-transparent lg:px-0 lg:py-0"
        >
          <ol
            ref={stripRef}
            onScroll={updateFades}
            style={{ maskImage: stripMask, WebkitMaskImage: stripMask }}
            className="relative flex flex-nowrap items-center gap-2 overflow-x-auto overscroll-x-contain px-0.5 py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden lg:flex-col lg:items-stretch lg:overflow-x-visible lg:px-0 lg:py-0"
          >
            {sections.map((entry, index) => {
              // Per-SECTION progress: the set-level figure repeated under
              // every section misleads (one answer in section 1 made
              // section 2 show the same %). Fall back to the set figure
              // only if the payload predates the per-section breakdown.
              const setProgress =
                progress?.sections?.[entry.setId]?.[entry.section.id] ??
                progress?.sets[entry.setId];
              const isActive = index === activeIndex;
              const isComplete = setProgress != null && setProgress.percent_complete >= 100;
              return (
                <li key={entry.section.id} className="min-w-0 shrink-0">
                  <button
                    type="button"
                    onClick={() => goToSection(index)}
                    aria-current={isActive ? 'step' : undefined}
                    className={cn(
                      'flex min-h-11 w-full items-center gap-2 rounded-full border px-3.5 py-2 text-left text-sm transition-colors',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500',
                      'lg:h-full lg:gap-3 lg:rounded-lf-control lg:px-3 lg:py-2.5',
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
                    <span className="flex min-w-0 items-center gap-1.5 lg:block lg:flex-1">
                      <span className="block whitespace-nowrap lg:truncate">
                        {entry.section.title}
                      </span>
                      {/* Chip completion (below lg): a check at 100%,
                          otherwise a compact percent. The sidebar keeps
                          the full "% complete" line + progress bar. */}
                      {setProgress &&
                        (isComplete ? (
                          <>
                            <Check
                              className="size-4 shrink-0 text-lf-success-600 lg:hidden"
                              aria-hidden="true"
                            />
                            <span className="sr-only lg:hidden">complete</span>
                          </>
                        ) : (
                          <>
                            <span
                              className={cn(
                                'shrink-0 text-xs font-normal lg:hidden',
                                // Active chip sits on bg-lf-primary-50:
                                // text-lf-text-subtle is only 4.25:1 there,
                                // below the 4.5:1 WCAG AA bar — use the
                                // darker muted tone on the tint.
                                isActive ? 'text-lf-text-muted' : 'text-lf-text-subtle',
                              )}
                            >
                              {/* Floor, never round: a chip reading
                                  "100%" is reserved for the check icon
                                  (a 99.6% section is not complete). */}
                              {Math.floor(setProgress.percent_complete)}%
                            </span>
                            <span className="sr-only lg:hidden">complete</span>
                          </>
                        ))}
                      {setProgress && (
                        <span
                          className={cn(
                            'hidden text-xs font-normal lg:block',
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
                          className="mt-1.5 hidden h-1 w-full overflow-hidden rounded-full bg-lf-accent-100 lg:block"
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

        <div className="min-w-0 space-y-6">
          {activeSection && (
            <div
              key={activeSection.section.id}
              ref={cardTopRef}
              className="scroll-mt-[8.5rem] lg:scroll-mt-28"
            >
              <Card className="lf-gradient-ring shadow-lf-card">
                <CardHeader>
                  <CardDescription>{activeSection.setTitle}</CardDescription>
                  <h2
                    ref={headingRef}
                    tabIndex={-1}
                    // Fallback scroll offset for the focus() call in
                    // goToSection, if a browser ignores preventScroll:
                    // ≈ the card wrapper's scroll-mt plus the heading's
                    // offset inside the card (border + CardHeader
                    // padding + description), so both scroll paths
                    // land the card in the same place.
                    className="scroll-mt-[11.5rem] text-xl font-semibold tracking-tight text-lf-text outline-none lg:scroll-mt-40"
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
            </div>
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
