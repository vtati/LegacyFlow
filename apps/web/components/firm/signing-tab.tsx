'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Circle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { ApiError, firmApi, type SigningChecklist } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';

/** Shared status summary for a signing checklist (firm + family views). */
export function signingSummary(checklist: SigningChecklist): {
  attested: number;
  total: number;
} {
  const attestedIds = new Set(checklist.attestations.map((a) => a.requirement_id));
  return {
    attested: checklist.items.filter((item) => attestedIds.has(item.key)).length,
    total: checklist.items.length,
  };
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

/**
 * Firm signing tab — checklist status for the matter, plus generation.
 *
 * Generation belongs on the firm side (R2): the API resolves the execution
 * profile from the matter's pack server-side (e.g. VA-WILL-V0) and only an
 * optional document_type assertion may be sent, so the action posts with
 * no document_type. It is offered when every package is released and no
 * checklist exists yet; once generated, the family Signing page renders
 * the same checklist via its own (family) client.
 */
export function SigningTab({ matterId }: { matterId: string }): React.JSX.Element {
  const [checklist, setChecklist] = useState<SigningChecklist | null>(null);
  const [documentsReleased, setDocumentsReleased] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  const loadChecklist = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      firmApi.getChecklist(matterId).catch((err: unknown) => {
        // A 404 means no checklist exists yet — an empty state, not an
        // error (same convention as the family Signing page).
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }),
      // Released state is read from the packages themselves (server truth),
      // not the parent page's possibly-stale matter status.
      firmApi.listPackages(matterId).catch(() => null),
    ])
      .then(([loadedChecklist, packages]) => {
        setChecklist(loadedChecklist);
        setDocumentsReleased(
          packages != null &&
            packages.length > 0 &&
            packages.every((p) => p.status === 'released'),
        );
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [matterId]);

  useEffect(() => {
    loadChecklist();
  }, [loadChecklist]);

  const handleGenerate = () => {
    setGenerating(true);
    setGenerateError(null);
    firmApi
      .generateChecklist(matterId)
      .then(setChecklist)
      .catch((err: unknown) =>
        setGenerateError(
          err instanceof Error ? err.message : 'Could not generate the signing checklist.',
        ),
      )
      .finally(() => setGenerating(false));
  };

  const attestedAtByRequirement = useMemo(() => {
    const map = new Map<string, string>();
    for (const attestation of checklist?.attestations ?? []) {
      map.set(attestation.requirement_id, attestation.attested_at);
    }
    return map;
  }, [checklist]);

  if (loading) return <LoadingState lines={4} label="Loading signing checklist…" />;
  if (error != null) return <ErrorState error={error} onRetry={loadChecklist} />;
  if (!checklist) {
    return (
      <div className="space-y-4">
        {generateError && (
          <p role="alert" className="text-sm text-lf-error-700">
            {generateError}
          </p>
        )}
        <EmptyState
          title="No checklist"
          description={
            documentsReleased
              ? 'The documents are released. Generate the signing checklist from this matter’s execution profile so the family can follow it on their Signing page.'
              : 'No signing checklist has been generated for this matter yet. Generation becomes available once the documents are released.'
          }
          action={
            documentsReleased ? (
              <Button type="button" disabled={generating} onClick={handleGenerate}>
                {generating ? 'Generating…' : 'Generate signing checklist'}
              </Button>
            ) : undefined
          }
        />
      </div>
    );
  }

  const { attested, total } = signingSummary(checklist);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Signing checklist</CardTitle>
          <CardDescription>
            Profile {checklist.execution_profile_id} · {attested} of {total} requirements attested
            by the client.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-lf-text-muted">
            Witnesses required:{' '}
            <strong className="text-lf-text">{getWitnessCount(checklist.requirements)}</strong>
            {' · '}Notary required:{' '}
            <strong className="text-lf-text">
              {isNotaryRequired(checklist.requirements) ? 'Yes' : 'No'}
            </strong>
          </p>
          {checklist.items.length === 0 ? (
            <EmptyState
              title="No requirements"
              description="The pack defines no signing requirements for this matter."
            />
          ) : (
            <ul className="divide-y divide-lf-border rounded-lf-control border border-lf-border">
              {checklist.items.map((item) => {
                const attestedAt = attestedAtByRequirement.get(item.key);
                return (
                  <li key={item.key} className="flex items-start gap-3 px-4 py-3">
                    {attestedAt ? (
                      <CheckCircle2
                        className="mt-0.5 size-5 shrink-0 text-lf-success-700"
                        aria-hidden="true"
                      />
                    ) : (
                      <Circle
                        className="mt-0.5 size-5 shrink-0 text-lf-text-subtle"
                        aria-hidden="true"
                      />
                    )}
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-lf-text">{item.label}</p>
                      {item.detail && <p className="text-sm text-lf-text-muted">{item.detail}</p>}
                      <p className="mt-0.5 text-xs text-lf-text-subtle">
                        {attestedAt ? `Attested ${formatDateTime(attestedAt)}` : 'Not yet attested'}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
