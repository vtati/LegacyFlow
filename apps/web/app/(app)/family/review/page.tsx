'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { DraftPanel } from '@/components/family/review/draft-panel';
import { ReviewAnswers } from '@/components/family/review/review-answers';
import { ReviewParkedItems } from '@/components/family/review/review-parked-items';
import { ReviewPersons } from '@/components/family/review/review-persons';
import { StatusPill } from '@/components/family/status-pill';
import {
  api,
  type AnswerMap,
  type AnswerValue,
  type DraftSetState,
  type HouseholdResponse,
  type IntakeProgressResponse,
  type Nomination,
  type NominationType,
  type ParkedItem,
  type Person,
  type QuestionSetsResponse,
} from '@/lib/api-client';

const NOMINATION_TYPES: NominationType[] = [
  'guardian',
  'executor',
  'trustee',
  'financial_agent',
  'healthcare_agent',
];

/**
 * Family review & drafts (self-serve draft generation, plan Part 2):
 * every answer as the documents will read it with inline edits, persons
 * & roles, then create watermarked drafts and submit them to the
 * attorney — all gated by the draft-set state endpoint (D5).
 */
export default function FamilyReviewPage(): React.JSX.Element {
  const { matters, loading: mattersLoading, error: mattersError, reload } = useMatters();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const matterId = selectedId ?? matters[0]?.id ?? null;
  const matter = matters.find((candidate) => candidate.id === matterId) ?? null;

  const [questionSets, setQuestionSets] = useState<QuestionSetsResponse | null>(null);
  const [progress, setProgress] = useState<IntakeProgressResponse | null>(null);
  const [answers, setAnswers] = useState<AnswerMap>({});
  const [household, setHousehold] = useState<HouseholdResponse | null>(null);
  const [nominations, setNominations] = useState<Nomination[]>([]);
  const [parkedItems, setParkedItems] = useState<ParkedItem[]>([]);
  const [draftState, setDraftState] = useState<DraftSetState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const [refreshError, setRefreshError] = useState<string | null>(null);

  const refreshDraftState = useCallback(async (id: string) => {
    try {
      setDraftState(await api.getDraftSetState(id));
      setRefreshError(null);
    } catch {
      // Keep the last good state (server-side gates still protect every
      // action), but say so — stale can_* flags must not look live.
      setRefreshError('Draft status could not be refreshed — reload to see the latest.');
    }
  }, []);

  const refreshProgress = useCallback(async (id: string) => {
    try {
      setProgress(await api.getIntakeProgress(id));
    } catch {
      /* header count only; the draft state refetch carries the gates */
    }
  }, []);

  const refreshNominations = useCallback(async (id: string) => {
    const results = await Promise.allSettled(
      NOMINATION_TYPES.map((type) => api.listNominations(id, type)),
    );
    setNominations(
      results.flatMap((result) => (result.status === 'fulfilled' ? result.value : [])),
    );
  }, []);

  const refreshParkedItems = useCallback(async (id: string) => {
    try {
      setParkedItems(await api.listParkedItems(id));
    } catch {
      /* open-items section only; the draft state carries the gates */
    }
  }, []);

  useEffect(() => {
    if (!matterId) return;
    let cancelled = false;
    setLoadError(null);
    setQuestionSets(null);
    setProgress(null);
    setHousehold(null);
    setDraftState(null);
    setParkedItems([]);
    Promise.allSettled([
      api.getQuestionSets(matterId),
      api.getAnswers(matterId),
      api.getIntakeProgress(matterId),
      api.getHousehold(matterId),
      api.getDraftSetState(matterId),
      api.listParkedItems(matterId),
      ...NOMINATION_TYPES.map((type) => api.listNominations(matterId, type)),
    ]).then((results) => {
      if (cancelled) return;
      const [sets, saved, prog, house, state, parked, ...nominationResults] = results;
      if (sets.status === 'fulfilled') setQuestionSets(sets.value);
      if (saved.status === 'fulfilled') setAnswers(saved.value.answers);
      if (prog.status === 'fulfilled') setProgress(prog.value);
      if (house.status === 'fulfilled') setHousehold(house.value);
      if (state.status === 'fulfilled') setDraftState(state.value);
      if (parked.status === 'fulfilled') setParkedItems(parked.value);
      setNominations(
        nominationResults.flatMap((result) =>
          result.status === 'fulfilled' ? result.value : [],
        ),
      );
      if (
        sets.status === 'rejected' ||
        state.status === 'rejected' ||
        saved.status === 'rejected' ||
        house.status === 'rejected'
      ) {
        // Answers/household failures matter as much as sets/state: without
        // them the page would present guesses as the family's answers.
        setLoadError('Some review details could not be loaded. Please refresh.');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [matterId]);

  const handleSaveAnswer = useCallback(
    async (key: string, value: AnswerValue) => {
      if (!matterId) return;
      await api.putAnswers(matterId, { [key]: value });
      setAnswers((current) => ({ ...current, [key]: value }));
      // Saving an answer can make existing drafts stale (D4), change the
      // completeness figure, re-point role nominations (they
      // materialize from answers server-side), and resolve a parked
      // item (an answer supersedes its marker) — refetch all four.
      await Promise.all([
        refreshDraftState(matterId),
        refreshProgress(matterId),
        refreshNominations(matterId),
        refreshParkedItems(matterId),
      ]);
    },
    [matterId, refreshDraftState, refreshProgress, refreshNominations, refreshParkedItems],
  );

  const handleSavePersonName = useCallback(
    async (person: Person, fullName: string) => {
      if (!matterId) return;
      const updated = await api.updatePerson(matterId, person.id, {
        full_name: fullName,
        kind: person.kind,
        date_of_birth: person.date_of_birth,
        email: person.email,
      });
      setHousehold((current) =>
        current
          ? {
              ...current,
              persons: current.persons.map((candidate) =>
                candidate.id === updated.id ? updated : candidate,
              ),
            }
          : current,
      );
      await refreshDraftState(matterId);
    },
    [matterId, refreshDraftState],
  );

  const handleGenerate = useCallback(async () => {
    if (!matterId) return;
    setGenerating(true);
    setActionError(null);
    try {
      await api.generateDraftSet(matterId);
      await refreshDraftState(matterId);
    } catch {
      setActionError('Draft creation failed — please try again.');
    } finally {
      setGenerating(false);
    }
  }, [matterId, refreshDraftState]);

  const handleSubmit = useCallback(async () => {
    if (!matterId) return;
    setSubmitting(true);
    setActionError(null);
    try {
      await api.submitDraftSet(matterId);
      await refreshDraftState(matterId);
      reload();
    } catch {
      setActionError(
        'Submission failed — your drafts may be out of date. Regenerate and try again.',
      );
      await refreshDraftState(matterId);
    } finally {
      setSubmitting(false);
    }
  }, [matterId, refreshDraftState, reload]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-lf-text">Review &amp; create your drafts</h1>
          <p className="mt-1 max-w-prose text-sm text-lf-text-muted">
            Check every answer exactly as it will appear in your documents. Fix anything
            that looks wrong, then create your drafts and send them to your attorney.
          </p>
        </div>
        <MatterSelect matters={matters} value={matterId} onChange={setSelectedId} />
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your plans…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reload} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          variant="aurora"
          title="No plans yet"
          description="When you start a plan, you can review its answers here."
          action={
            <Button asChild>
              <Link href="/family/intake">Start your plan</Link>
            </Button>
          }
        />
      )}

      {matter && (
        <div className="space-y-6">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold text-lf-text">{matter.title}</h2>
              <StatusPill status={matter.status} />
              {progress && (
                <span className="text-sm text-lf-text-muted">
                  {progress.overall.required_answered} of{' '}
                  {progress.overall.required_total} required answers complete
                </span>
              )}
            </div>
            {loadError && (
              <p role="alert" className="text-sm text-lf-error-700">
                {loadError}
              </p>
            )}
          </div>

          {questionSets && (
            <ReviewAnswers
              questionSets={questionSets}
              answers={answers}
              persons={household?.persons ?? []}
              onSaveAnswer={handleSaveAnswer}
            />
          )}

          {household && (
            <ReviewPersons
              persons={household.persons}
              nominations={nominations}
              onSavePersonName={handleSavePersonName}
            />
          )}

          {matterId && <ReviewParkedItems matterId={matterId} items={parkedItems} />}

          {refreshError && (
            <p role="alert" className="text-sm text-lf-error-700">
              {refreshError}
            </p>
          )}

          {draftState && matterId && (
            <DraftPanel
              state={draftState}
              generating={generating}
              submitting={submitting}
              actionError={actionError}
              onGenerate={handleGenerate}
              onSubmit={handleSubmit}
            />
          )}

          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-6">
              <p className="text-sm text-lf-text-muted">
                Your created drafts appear in your documents, marked as drafts.
              </p>
              <Button asChild variant="outline">
                <Link href="/family/documents">Go to your documents</Link>
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
