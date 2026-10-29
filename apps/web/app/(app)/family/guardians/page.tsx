'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Modal } from '@/components/ui/modal';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import {
  api,
  type Nomination,
  type NominationInput,
  type NominationType,
  type Person,
} from '@/lib/api-client';

const TRUSTED_ROLE_TYPES: NominationType[] = [
  'trustee',
  'executor',
  'financial_agent',
  'healthcare_agent',
];

const ROLE_LABELS: Record<NominationType, string> = {
  guardian: 'Guardian',
  trustee: 'Trustee',
  executor: 'Executor',
  financial_agent: 'Financial agent',
  healthcare_agent: 'Healthcare agent',
};

function roleLabel(type: string): string {
  return type in ROLE_LABELS ? ROLE_LABELS[type as NominationType] : type;
}

interface NominateFormProps {
  persons: Person[];
  types: NominationType[];
  saving: boolean;
  formError: string | null;
  onSubmit: (input: NominationInput) => void;
  onCancel: () => void;
  idPrefix: string;
}

/** Shared nominate form: person, priority, notes, and a role-type selector. */
function NominateForm({
  persons,
  types,
  saving,
  formError,
  onSubmit,
  onCancel,
  idPrefix,
}: NominateFormProps) {
  const [type, setType] = useState<NominationType>(types[0] ?? 'guardian');
  const [personId, setPersonId] = useState('');
  const [priority, setPriority] = useState('1');
  const [notes, setNotes] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!personId) {
      setValidationError('Please choose a person.');
      return;
    }
    const parsedPriority = Number.parseInt(priority, 10);
    if (!Number.isInteger(parsedPriority) || parsedPriority < 1) {
      setValidationError('Priority must be a whole number of 1 or higher.');
      return;
    }
    setValidationError(null);
    onSubmit({
      nomination_type: type,
      person_id: personId,
      priority: parsedPriority,
      notes: notes.trim() || null,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-type`}>Role</Label>
          <select
            id={`${idPrefix}-type`}
            value={type}
            onChange={(event) => setType(event.target.value as NominationType)}
            className="h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            {types.map((option) => (
              <option key={option} value={option}>
                {roleLabel(option)}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-person`}>Person</Label>
          <select
            id={`${idPrefix}-person`}
            value={personId}
            onChange={(event) => setPersonId(event.target.value)}
            className="h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            <option value="">Choose a person…</option>
            {persons.map((person) => (
              <option key={person.id} value={person.id}>
                {person.full_name}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-priority`}>Priority</Label>
          <Input
            id={`${idPrefix}-priority`}
            type="number"
            min={1}
            step={1}
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          />
          <p className="text-xs text-lf-text-subtle">
            1 is your first choice, 2 the backup, and so on.
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-notes`}>Notes</Label>
          <textarea
            id={`${idPrefix}-notes`}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            rows={2}
            placeholder="Optional — anything your attorney should know"
            className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          />
        </div>
      </div>
      {(validationError ?? formError) && (
        <p role="alert" className="text-sm text-lf-error-700">
          {validationError ?? formError}
        </p>
      )}
      <div className="flex gap-3">
        <Button type="submit" disabled={saving}>
          {saving ? 'Saving…' : 'Add nomination'}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

interface NominationListProps {
  nominations: Nomination[];
  persons: Person[];
  withdrawingId: string | null;
  onWithdraw: (nomination: Nomination) => void;
  emptyTitle: string;
  emptyDescription: string;
}

function NominationList({
  nominations,
  persons,
  withdrawingId,
  onWithdraw,
  emptyTitle,
  emptyDescription,
}: NominationListProps) {
  if (nominations.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }
  return (
    <ul className="space-y-3">
      {nominations.map((nomination) => {
        const person = persons.find((p) => p.id === nomination.person_id);
        return (
          <li key={nomination.id}>
            <Card>
              <CardContent className="flex items-start justify-between gap-3 p-6">
                <div>
                  <p className="font-semibold text-lf-text">
                    <span
                      className="mr-2 inline-flex size-6 items-center justify-center rounded-full bg-lf-primary-50 text-xs font-semibold text-lf-primary-800"
                      aria-label={`Priority ${nomination.priority}`}
                    >
                      {nomination.priority}
                    </span>
                    {person?.full_name ?? 'Unknown person'}
                  </p>
                  <p className="mt-1 text-sm text-lf-text-muted">
                    {roleLabel(nomination.nomination_type)} · {nomination.status}
                  </p>
                  {nomination.notes && (
                    <p className="mt-1 text-sm text-lf-text-muted">{nomination.notes}</p>
                  )}
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={withdrawingId === nomination.id}
                  onClick={() => onWithdraw(nomination)}
                >
                  {withdrawingId === nomination.id ? 'Withdrawing…' : 'Withdraw'}
                </Button>
              </CardContent>
            </Card>
          </li>
        );
      })}
    </ul>
  );
}

/** Guardians — guardian nominations plus trusted-role nominations. */
export default function FamilyGuardiansPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [nominations, setNominations] = useState<Nomination[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [showGuardianForm, setShowGuardianForm] = useState(false);
  const [showRoleForm, setShowRoleForm] = useState(false);
  const [formSaving, setFormSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [withdrawingId, setWithdrawingId] = useState<string | null>(null);
  const [withdrawTarget, setWithdrawTarget] = useState<Nomination | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadData = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.getHousehold(id),
      ...(['guardian', ...TRUSTED_ROLE_TYPES] as NominationType[]).map((type) =>
        api.listNominations(id, type).catch(() => [] as Nomination[]),
      ),
    ])
      .then(([household, ...nominationLists]) => {
        setPersons(household.persons);
        setNominations(nominationLists.flat());
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  useEffect(() => {
    if (matterId) loadData(matterId);
  }, [matterId, loadData]);

  const guardians = useMemo(
    () =>
      nominations
        .filter((n) => n.nomination_type === 'guardian')
        .sort((a, b) => a.priority - b.priority),
    [nominations],
  );

  const trustedRoles = useMemo(
    () =>
      nominations
        .filter((n) => n.nomination_type !== 'guardian')
        .sort(
          (a, b) => a.nomination_type.localeCompare(b.nomination_type) || a.priority - b.priority,
        ),
    [nominations],
  );

  const handleNominate = (input: NominationInput, closeForm: () => void) => {
    if (!matterId) return;
    setFormSaving(true);
    setFormError(null);
    setNotice(null);
    api
      .createNomination(matterId, input)
      .then(() => {
        closeForm();
        setNotice(`${roleLabel(input.nomination_type)} nomination saved.`);
        loadData(matterId);
      })
      .catch((err: unknown) =>
        setFormError(err instanceof Error ? err.message : 'Could not save the nomination.'),
      )
      .finally(() => setFormSaving(false));
  };

  // Confirmation goes through the in-app Modal, never window.confirm:
  // browsing contexts that suppress native dialogs make confirm() return
  // false without showing anything, so the click silently did nothing.
  const handleWithdraw = (nomination: Nomination) => {
    setWithdrawTarget(nomination);
  };

  const confirmWithdraw = () => {
    const nomination = withdrawTarget;
    if (!nomination || !matterId) return;
    setWithdrawingId(nomination.id);
    setNotice(null);
    api
      .deleteNomination(matterId, nomination.id)
      .then(() => {
        setNotice('Nomination withdrawn.');
        setWithdrawTarget(null);
        loadData(matterId);
      })
      .catch((err: unknown) => {
        setNotice(
          err instanceof Error
            ? `Could not withdraw: ${err.message}`
            : 'Could not withdraw the nomination.',
        );
        setWithdrawTarget(null);
      })
      .finally(() => setWithdrawingId(null));
  };

  const withdrawTargetPerson = withdrawTarget
    ? persons.find((p) => p.id === withdrawTarget.person_id)
    : null;

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Guardians</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Name the people you trust to care for your children and to handle key roles. Your attorney
          will review every choice — nothing here is final until they approve it.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your guardian nominations will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {matterId && loading && <LoadingState lines={4} label="Loading nominations…" />}
      {error != null && matterId != null && !loading ? (
        <ErrorState error={error} onRetry={() => loadData(matterId)} />
      ) : null}

      {notice && (
        <p
          role="status"
          className="rounded-lf-control border border-lf-success-600 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
        >
          {notice}
        </p>
      )}

      {matterId && !loading && !error && (
        <>
          <section aria-labelledby="guardians-heading" className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h2 id="guardians-heading" className="text-lg font-semibold text-lf-text">
                Guardian nominations
              </h2>
              {!showGuardianForm && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setShowGuardianForm(true)}
                >
                  Nominate a guardian
                </Button>
              )}
            </div>
            {showGuardianForm && (
              <Card>
                <CardHeader>
                  <CardTitle>Nominate a guardian</CardTitle>
                  <CardDescription>
                    Who should care for your children if you cannot? List your first choice as
                    priority 1.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <NominateForm
                    persons={persons}
                    types={['guardian']}
                    saving={formSaving}
                    formError={formError}
                    onSubmit={(input) => handleNominate(input, () => setShowGuardianForm(false))}
                    onCancel={() => setShowGuardianForm(false)}
                    idPrefix="nominate-guardian"
                  />
                </CardContent>
              </Card>
            )}
            <NominationList
              nominations={guardians}
              persons={persons}
              withdrawingId={withdrawingId}
              onWithdraw={handleWithdraw}
              emptyTitle="No guardians nominated yet"
              emptyDescription="Nominate the person you would want caring for your children. You can list backups in priority order."
            />
          </section>

          <section aria-labelledby="trusted-roles-heading" className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h2 id="trusted-roles-heading" className="text-lg font-semibold text-lf-text">
                Trusted roles
              </h2>
              {!showRoleForm && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setShowRoleForm(true)}
                >
                  Nominate for a role
                </Button>
              )}
            </div>
            <p className="text-sm text-lf-text-muted">
              Trustee, executor, financial agent, and healthcare agent — the people who would carry
              out your wishes and manage affairs on your behalf.
            </p>
            {showRoleForm && (
              <Card>
                <CardHeader>
                  <CardTitle>Nominate for a trusted role</CardTitle>
                  <CardDescription>
                    Choose the role, the person, and their priority order.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <NominateForm
                    persons={persons}
                    types={TRUSTED_ROLE_TYPES}
                    saving={formSaving}
                    formError={formError}
                    onSubmit={(input) => handleNominate(input, () => setShowRoleForm(false))}
                    onCancel={() => setShowRoleForm(false)}
                    idPrefix="nominate-role"
                  />
                </CardContent>
              </Card>
            )}
            <NominationList
              nominations={trustedRoles}
              persons={persons}
              withdrawingId={withdrawingId}
              onWithdraw={handleWithdraw}
              emptyTitle="No trusted roles nominated yet"
              emptyDescription="Name the people you trust with financial and healthcare decisions. You can change these any time before your attorney finalizes the plan."
            />
          </section>
        </>
      )}

      <Modal
        open={withdrawTarget != null}
        onClose={() => setWithdrawTarget(null)}
        title="Withdraw this nomination?"
        description={
          withdrawTarget
            ? `The ${roleLabel(withdrawTarget.nomination_type)} nomination for ${withdrawTargetPerson?.full_name ?? 'this person'} will be withdrawn. This cannot be undone — you would need to nominate them again.`
            : undefined
        }
      >
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="outline" onClick={() => setWithdrawTarget(null)}>
            Cancel
          </Button>
          <Button
            type="button"
            onClick={confirmWithdraw}
            disabled={withdrawTarget != null && withdrawingId === withdrawTarget.id}
            className="bg-lf-error-600 text-white hover:bg-lf-error-700"
          >
            {withdrawTarget != null && withdrawingId === withdrawTarget.id
              ? 'Withdrawing…'
              : 'Withdraw nomination'}
          </Button>
        </div>
      </Modal>
    </div>
  );
}
