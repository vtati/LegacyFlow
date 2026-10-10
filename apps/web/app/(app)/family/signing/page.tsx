'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Circle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { signingSummary } from '@/components/firm/signing-tab';
import { api, ApiError, type SigningChecklist, type SigningChecklistItem } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';

/**
 * Non-certifying acknowledgement language. Checking this box is a bookkeeping
 * step — it is not a legal certification of execution.
 */
const ACKNOWLEDGEMENT =
  'I have read this requirement and understand I must follow my attorney’s instructions for it.';

const NON_CERTIFYING_NOTE =
  'Checking this box is not a legal certification — it only marks the requirement reviewed in this app. Your attorney confirms proper execution.';

interface RequirementRowProps {
  requirement: SigningChecklistItem;
  attestedAt: string | undefined;
  attesting: boolean;
  error: string | null;
  onAttest: (requirement: SigningChecklistItem, acknowledged: boolean) => void;
}

/** Extract witness count from the backend's requirements dict. */
function getWitnessCount(requirements: Record<string, unknown>): number {
  const witnesses = requirements['witnesses'] as Record<string, unknown> | undefined;
  const count = witnesses?.['required_count'];
  return typeof count === 'number' ? count : 0;
}

/** Extract notary requirement from the backend's requirements dict. */
function isNotaryRequired(requirements: Record<string, unknown>): boolean {
  const notary = requirements['notary'] as Record<string, unknown> | undefined;
  return notary?.['legally_required'] === true;
}

function RequirementRow({
  requirement,
  attestedAt,
  attesting,
  error,
  onAttest,
}: RequirementRowProps) {
  const [acknowledged, setAcknowledged] = useState(false);

  if (attestedAt) {
    return (
      <li className="flex items-start gap-3 px-4 py-4">
        <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-lf-success-700" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-lf-text">{requirement.label}</p>
          {requirement.detail && <p className="text-sm text-lf-text-muted">{requirement.detail}</p>}
          <p className="mt-0.5 text-xs text-lf-text-subtle">
            Marked reviewed {formatDateTime(attestedAt)}
          </p>
        </div>
      </li>
    );
  }

  return (
    <li className="space-y-3 px-4 py-4">
      <div className="flex items-start gap-3">
        <Circle className="mt-0.5 size-5 shrink-0 text-lf-text-subtle" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-lf-text">{requirement.label}</p>
          {requirement.detail && <p className="text-sm text-lf-text-muted">{requirement.detail}</p>}
        </div>
      </div>
      <label className="flex cursor-pointer items-start gap-3 rounded-lf-control bg-lf-canvas-subtle px-3 py-2.5">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(event) => setAcknowledged(event.target.checked)}
          className="mt-0.5 size-4 shrink-0 accent-lf-primary-700"
        />
        <span className="text-sm text-lf-text-muted">{ACKNOWLEDGEMENT}</span>
      </label>
      <p className="text-xs text-lf-text-subtle">{NON_CERTIFYING_NOTE}</p>
      {error && (
        <p role="alert" className="text-sm text-lf-error-700">
          {error}
        </p>
      )}
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!acknowledged || attesting}
        onClick={() => onAttest(requirement, acknowledged)}
      >
        {attesting ? 'Saving…' : 'Mark reviewed'}
      </Button>
    </li>
  );
}

/** Family signing — pack checklist with non-certifying attestation checkboxes. */
export default function FamilySigningPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [checklist, setChecklist] = useState<SigningChecklist | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [attestingId, setAttestingId] = useState<string | null>(null);
  const [attestErrors, setAttestErrors] = useState<Record<string, string>>({});

  const loadChecklist = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .getChecklist(id)
      .then(setChecklist)
      .catch((err: unknown) => {
        // A 404 means no checklist exists yet — normal for in-progress matters.
        // Show a friendly empty state, not an error.
        if (err instanceof ApiError && err.status === 404) {
          setChecklist(null);
        } else {
          setError(err);
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  useEffect(() => {
    if (matterId) loadChecklist(matterId);
  }, [matterId, loadChecklist]);

  const attestedAtByRequirement = useMemo(() => {
    const map = new Map<string, string>();
    for (const attestation of checklist?.attestations ?? []) {
      map.set(attestation.requirement_id, attestation.attested_at);
    }
    return map;
  }, [checklist]);

  const handleAttest = (requirement: SigningChecklistItem, acknowledged: boolean) => {
    if (!matterId || !acknowledged || !checklist) return;
    setAttestingId(requirement.key);
    setAttestErrors((prev) => ({ ...prev, [requirement.key]: '' }));
    api
      .attest(checklist.id, { requirement_id: requirement.key, acknowledgement: ACKNOWLEDGEMENT })
      .then((attestation) => {
        setChecklist((prev) =>
          prev ? { ...prev, attestations: [...prev.attestations, attestation] } : prev,
        );
      })
      .catch((err: unknown) =>
        setAttestErrors((prev) => ({
          ...prev,
          [requirement.key]:
            err instanceof Error ? err.message : 'Could not save. Please try again.',
        })),
      )
      .finally(() => setAttestingId(null));
  };

  const summary = checklist ? signingSummary(checklist) : null;

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Signing</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          What has to happen when you sign, straight from your jurisdiction pack. Your attorney
          walks you through the real signing — this checklist just keeps you prepared.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your signing checklist will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {matterId && loading && <LoadingState lines={4} label="Loading signing checklist…" />}
      {error != null && matterId != null && !loading ? (
        <ErrorState error={error} onRetry={() => loadChecklist(matterId)} />
      ) : null}

      {matterId && !loading && !error && !checklist && (
        <EmptyState
          title="No signing checklist yet"
          description="Your signing checklist will be ready once your documents are finalized. Check back after your attorney reviews your plan."
        />
      )}

      {matterId && !loading && !error && checklist && (
        <Card>
          <CardHeader>
            <CardTitle>Signing checklist</CardTitle>
            <CardDescription>
              Profile {checklist.execution_profile_id}
              {summary ? ` · ${summary.attested} of ${summary.total} reviewed` : ''}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 p-0 pt-0 sm:p-0">
            <div className="grid gap-3 px-4 pt-4 sm:grid-cols-2 sm:px-6">
              <div className="rounded-lf-control border border-lf-border px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-lf-text-subtle">
                  Witnesses
                </p>
                <p className="mt-1 text-lg font-semibold text-lf-text">
                  {getWitnessCount(checklist.requirements)}{' '}
                  {getWitnessCount(checklist.requirements) === 1 ? 'witness' : 'witnesses'}
                </p>
              </div>
              <div className="rounded-lf-control border border-lf-border px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-lf-text-subtle">
                  Notary
                </p>
                <p className="mt-1 text-lg font-semibold text-lf-text">
                  {isNotaryRequired(checklist.requirements) ? 'Required' : 'Not required'}
                </p>
              </div>
            </div>
            {checklist.items.length === 0 ? (
              <div className="px-4 pb-4 sm:px-6">
                <EmptyState
                  title="No requirements"
                  description="The pack defines no signing requirements for this matter."
                />
              </div>
            ) : (
              <ul className="divide-y divide-lf-border border-t border-lf-border">
                {checklist.items.map((item) => (
                  <RequirementRow
                    key={item.key}
                    requirement={item}
                    attestedAt={attestedAtByRequirement.get(item.key)}
                    attesting={attestingId === item.key}
                    error={attestErrors[item.key] || null}
                    onAttest={handleAttest}
                  />
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
