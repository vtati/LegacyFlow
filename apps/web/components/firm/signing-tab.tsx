'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Circle } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type SigningChecklist } from '@/lib/api-client';
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

/** Firm signing tab — read-only checklist status for the matter. */
export function SigningTab({ matterId }: { matterId: string }): React.JSX.Element {
  const [checklist, setChecklist] = useState<SigningChecklist | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const loadChecklist = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .getChecklist(matterId)
      .then(setChecklist)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [matterId]);

  useEffect(() => {
    loadChecklist();
  }, [loadChecklist]);

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
      <EmptyState
        title="No checklist"
        description="No signing checklist has been generated for this matter yet."
      />
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
