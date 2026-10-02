'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { PersonForm, type PersonFormValues } from '@/components/family/person-form';
import { api, type HouseholdResponse } from '@/lib/api-client';

/** Dependents — minor persons in the household, with an add-minor form. */
export default function FamilyDependentsPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [household, setHousehold] = useState<HouseholdResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [addSaving, setAddSaving] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadHousehold = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .getHousehold(id)
      .then((response) => setHousehold(response))
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  useEffect(() => {
    if (matterId) loadHousehold(matterId);
  }, [matterId, loadHousehold]);

  const minors = (household?.persons ?? []).filter((person) => person.kind === 'minor');

  const handleAdd = (values: PersonFormValues) => {
    if (!matterId) return;
    setAddSaving(true);
    setAddError(null);
    setNotice(null);
    api
      .createPerson(matterId, {
        full_name: values.full_name.trim(),
        date_of_birth: values.date_of_birth || null,
        email: values.email.trim() || null,
        kind: 'minor',
      })
      .then(() => {
        setShowAdd(false);
        setNotice('Minor added to your household.');
        loadHousehold(matterId);
      })
      .catch((err: unknown) =>
        setAddError(err instanceof Error ? err.message : 'Could not add the minor.'),
      )
      .finally(() => setAddSaving(false));
  };

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Dependents</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Children and other minors in your household. Your attorney uses this list when naming
          guardians.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your dependents will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {matterId && loading && <LoadingState lines={3} label="Loading dependents…" />}
      {error != null && matterId != null && !loading ? (
        <ErrorState error={error} onRetry={() => loadHousehold(matterId)} />
      ) : null}

      {notice && (
        <p
          role="status"
          className="rounded-lf-control border border-lf-success-600 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
        >
          {notice}
        </p>
      )}

      {household && !loading && (
        <>
          {minors.length === 0 ? (
            <EmptyState
              title="No minors listed"
              description="If you have children or other minors who depend on you, add them so your attorney can plan for their care."
            />
          ) : (
            <div className="space-y-4">
              {minors.map((person) => (
                <Card key={person.id}>
                  <CardContent className="p-6">
                    <p className="font-semibold text-lf-text">{person.full_name}</p>
                    {person.date_of_birth && (
                      <p className="text-sm text-lf-text-muted">Born {person.date_of_birth}</p>
                    )}
                    {person.email && <p className="text-sm text-lf-text-muted">{person.email}</p>}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}

          {!showAdd ? (
            <Button type="button" variant="outline" onClick={() => setShowAdd(true)}>
              Add a minor
            </Button>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>Add a minor</CardTitle>
                <CardDescription>
                  Only add people your firm knows about — the details should match your records.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <PersonForm
                  kindOptions={[]}
                  fixedKind="minor"
                  saving={addSaving}
                  formError={addError}
                  submitLabel="Add minor"
                  idPrefix="add-minor"
                  onSubmit={handleAdd}
                  onCancel={() => setShowAdd(false)}
                />
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
