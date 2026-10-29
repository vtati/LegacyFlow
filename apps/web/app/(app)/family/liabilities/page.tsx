'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Modal } from '@/components/ui/modal';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { LiabilityForm, liabilityTypeLabel } from '@/components/family/liability-form';
import {
  api,
  type Liability,
  type LiabilityInput,
  type LiabilityPatchInput,
} from '@/lib/api-client';
import { formatMoney } from '@/lib/format';

interface LiabilityRowProps {
  liability: Liability;
  onEdit: (liability: Liability) => void;
  onDelete: (liability: Liability) => void;
  deleting: boolean;
}

function LiabilityRow({ liability, onEdit, onDelete, deleting }: LiabilityRowProps) {
  return (
    <tr className="border-t border-lf-border">
      <td className="px-4 py-3">
        <p className="font-medium text-lf-text">{liability.label}</p>
        <p className="mt-0.5 text-xs text-lf-text-subtle">
          {liabilityTypeLabel(liability.liability_type)}
        </p>
      </td>
      <td className="px-4 py-3 text-sm text-lf-text-muted">
        {formatMoney(liability.balance_cents)}
      </td>
      <td className="px-4 py-3 text-sm text-lf-text-muted">
        {formatMoney(liability.monthly_payment_cents)}
      </td>
      <td className="px-4 py-3">
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => onEdit(liability)}>
            Edit
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={deleting}
            onClick={() => onDelete(liability)}
            className="text-lf-error-700 hover:text-lf-error-700"
          >
            {deleting ? 'Deleting…' : 'Delete'}
          </Button>
        </div>
      </td>
    </tr>
  );
}

/** Family liabilities — list, create, edit, delete. */
export default function FamilyLiabilitiesPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [liabilities, setLiabilities] = useState<Liability[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Liability | null>(null);
  const [formSaving, setFormSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Liability | null>(null);

  const householdId = useMemo(
    () => matters.find((matter) => matter.id === matterId)?.household?.id ?? null,
    [matters, matterId],
  );

  const loadData = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .listLiabilities(id)
      .then((response) => setLiabilities(response.items))
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  useEffect(() => {
    if (householdId) loadData(householdId);
  }, [householdId, loadData]);

  const totalBalanceCents = useMemo(
    () => liabilities.reduce((sum, liability) => sum + liability.balance_cents, 0),
    [liabilities],
  );

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (liability: Liability) => {
    setEditing(liability);
    setFormError(null);
    setFormOpen(true);
  };

  const handleFormSubmit = (input: LiabilityInput | LiabilityPatchInput) => {
    if (!householdId) return;
    setFormSaving(true);
    setFormError(null);
    // When editing is null the form always produces a full LiabilityInput.
    const request = editing
      ? api.updateLiability(editing.id, input)
      : api.createLiability(householdId, {
          ...(input as LiabilityInput),
          matter_id: matterId,
        });
    request
      .then((saved) => {
        setFormOpen(false);
        setEditing(null);
        setNotice(editing ? `“${saved.label}” updated.` : `“${saved.label}” added.`);
        loadData(householdId);
      })
      .catch((err: unknown) =>
        setFormError(err instanceof Error ? err.message : 'Could not save the liability.'),
      )
      .finally(() => setFormSaving(false));
  };

  // Confirmation goes through the in-app Modal, never window.confirm:
  // browsing contexts that suppress native dialogs make confirm() return
  // false without showing anything, so the click silently did nothing.
  const handleDelete = (liability: Liability) => {
    setDeleteTarget(liability);
  };

  const confirmDelete = () => {
    const liability = deleteTarget;
    if (!liability || !householdId) return;
    setDeletingId(liability.id);
    setNotice(null);
    api
      .deleteLiability(liability.id)
      .then(() => {
        setNotice(`“${liability.label}” deleted.`);
        setDeleteTarget(null);
        loadData(householdId);
      })
      .catch((err: unknown) => {
        setNotice(
          err instanceof Error
            ? `Could not delete: ${err.message}`
            : 'Could not delete the liability.',
        );
        setDeleteTarget(null);
      })
      .finally(() => setDeletingId(null));
  };

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Liabilities</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Mortgages, loans, and credit cards — what you owe, so your plan reflects the full picture.
          Your attorney reviews everything before it is finalized.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your liabilities will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {householdId && loading && <LoadingState lines={4} label="Loading liabilities…" />}
      {error != null && householdId != null && !loading ? (
        <ErrorState error={error} onRetry={() => loadData(householdId)} />
      ) : null}

      {notice && (
        <p
          role="status"
          className="rounded-lf-control border border-lf-success-600 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
        >
          {notice}
        </p>
      )}

      {householdId && !loading && !error && (
        <section aria-label="Liabilities" className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-lf-text-muted">
              {liabilities.length === 0
                ? 'No liabilities listed yet.'
                : `${liabilities.length} ${liabilities.length === 1 ? 'liability' : 'liabilities'} · total balance ${formatMoney(totalBalanceCents)}`}
            </p>
            <Button type="button" size="sm" onClick={openCreate}>
              Add liability
            </Button>
          </div>

          {liabilities.length === 0 ? (
            <EmptyState
              title="No liabilities yet"
              description="Add mortgages, loans, credit cards, and anything else you owe. If you are debt-free, you can leave this empty."
              action={
                <Button type="button" onClick={openCreate}>
                  Add your first liability
                </Button>
              }
            />
          ) : (
            <Card>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-sm">
                  <caption className="sr-only">Liabilities</caption>
                  <thead>
                    <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                      <th scope="col" className="px-4 py-3 font-semibold">
                        Liability
                      </th>
                      <th scope="col" className="px-4 py-3 font-semibold">
                        Balance
                      </th>
                      <th scope="col" className="px-4 py-3 font-semibold">
                        Monthly payment
                      </th>
                      <th scope="col" className="px-4 py-3 font-semibold">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {liabilities.map((liability) => (
                      <LiabilityRow
                        key={liability.id}
                        liability={liability}
                        onEdit={openEdit}
                        onDelete={handleDelete}
                        deleting={deletingId === liability.id}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </section>
      )}

      <Modal
        open={deleteTarget != null}
        onClose={() => setDeleteTarget(null)}
        title="Delete this liability?"
        description={
          deleteTarget
            ? `“${deleteTarget.label}” will be removed from your plan. This cannot be undone.`
            : undefined
        }
      >
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="outline" onClick={() => setDeleteTarget(null)}>
            Cancel
          </Button>
          <Button
            type="button"
            onClick={confirmDelete}
            disabled={deleteTarget != null && deletingId === deleteTarget.id}
            className="bg-lf-error-600 text-white hover:bg-lf-error-700"
          >
            {deleteTarget != null && deletingId === deleteTarget.id
              ? 'Deleting…'
              : 'Delete liability'}
          </Button>
        </div>
      </Modal>

      <LiabilityForm
        key={editing ? `edit-${editing.id}` : 'new'}
        open={formOpen}
        liability={editing}
        saving={formSaving}
        formError={formError}
        onSubmit={handleFormSubmit}
        onClose={() => {
          setFormOpen(false);
          setEditing(null);
        }}
      />
    </div>
  );
}
