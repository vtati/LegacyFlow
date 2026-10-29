'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Modal } from '@/components/ui/modal';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { AssetForm, assetTypeLabel, ownershipLabel } from '@/components/family/asset-form';
import { BeneficiaryEditor } from '@/components/family/beneficiary-editor';
import {
  api,
  type Asset,
  type AssetFlag,
  type AssetInput,
  type AssetPatchInput,
  type BeneficiaryDesignation,
  type RuleSeverity,
} from '@/lib/api-client';
import { formatMoney } from '@/lib/format';
import { cn } from '@/lib/utils';

function flagBadgeClass(severity: RuleSeverity): string {
  const normalized = severity.toLowerCase();
  if (normalized === 'blocking') return 'bg-lf-error-100 text-lf-error-700';
  if (normalized === 'review') return 'bg-lf-accent-100 text-lf-accent-800';
  return 'bg-lf-primary-50 text-lf-primary-800';
}

function FlagBadge({ flag }: { flag: AssetFlag }) {
  return (
    <span
      className={cn(
        'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
        flagBadgeClass(flag.severity),
      )}
    >
      {flag.message}
    </span>
  );
}

function designationSummary(designation: BeneficiaryDesignation | null | undefined): {
  label: string;
  className: string;
} {
  // Defensive: the API may return partial/malformed designations (e.g. {} or
  // string values from legacy data). Only count real arrays.
  const primary = Array.isArray(designation?.primary) ? designation.primary.length : 0;
  const contingent = Array.isArray(designation?.contingent) ? designation.contingent.length : 0;
  if (primary === 0 && contingent === 0) {
    return { label: 'No beneficiaries', className: 'bg-lf-accent-100 text-lf-accent-800' };
  }
  return {
    label: `${primary} primary · ${contingent} contingent`,
    className: 'bg-lf-success-100 text-lf-success-700',
  };
}

interface AssetCardProps {
  asset: Asset;
  flags: AssetFlag[];
  evaluating: boolean;
  deleting: boolean;
  onEdit: (asset: Asset) => void;
  onBeneficiaries: (asset: Asset) => void;
  onDelete: (asset: Asset) => void;
  onEvaluate: (asset: Asset) => void;
}

function AssetCard({
  asset,
  flags,
  evaluating,
  deleting,
  onEdit,
  onBeneficiaries,
  onDelete,
  onEvaluate,
}: AssetCardProps) {
  const status = designationSummary(asset.beneficiary_designation);
  return (
    <Card>
      <CardContent className="space-y-3 p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-lf-text">{asset.label}</p>
            <p className="mt-0.5 text-xs text-lf-text-subtle">{assetTypeLabel(asset.asset_type)}</p>
          </div>
          <p className="shrink-0 text-base font-semibold text-lf-text">
            {formatMoney(asset.estimated_value_cents)}
          </p>
        </div>
        <p className="text-sm text-lf-text-muted">Ownership: {ownershipLabel(asset.ownership)}</p>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
              status.className,
            )}
          >
            {status.label}
          </span>
          {flags.map((flag) => (
            <FlagBadge key={flag.id} flag={flag} />
          ))}
        </div>
        {flags.length === 0 && (
          <p className="text-xs text-lf-text-subtle">
            Run an alignment check to surface anything your attorney should review.
          </p>
        )}
        {asset.notes && <p className="text-sm text-lf-text-muted">{asset.notes}</p>}
        <div className="flex flex-wrap gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" onClick={() => onEdit(asset)}>
            Edit
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={() => onBeneficiaries(asset)}>
            Beneficiaries
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={evaluating}
            onClick={() => onEvaluate(asset)}
          >
            {evaluating ? 'Checking…' : 'Check alignment'}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={deleting}
            onClick={() => onDelete(asset)}
            className="text-lf-error-700 hover:text-lf-error-700"
          >
            {deleting ? 'Deleting…' : 'Delete'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** Family assets — list, create, edit, delete, beneficiary designations, alignment flags. */
export default function FamilyAssetsPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [flagsByAsset, setFlagsByAsset] = useState<Record<string, AssetFlag[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Asset | null>(null);
  const [formSaving, setFormSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [beneficiariesOpen, setBeneficiariesOpen] = useState(false);
  const [beneficiaryAsset, setBeneficiaryAsset] = useState<Asset | null>(null);
  const [beneficiariesSaving, setBeneficiariesSaving] = useState(false);
  const [beneficiariesError, setBeneficiariesError] = useState<string | null>(null);

  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Asset | null>(null);
  const [evaluatingId, setEvaluatingId] = useState<string | null>(null);

  const householdId = useMemo(
    () => matters.find((matter) => matter.id === matterId)?.household?.id ?? null,
    [matters, matterId],
  );

  const loadData = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .listAssets(id)
      .then((response) => {
        setAssets(response.items);
        setFlagsByAsset({});
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
    if (householdId) loadData(householdId);
  }, [householdId, loadData]);

  const totalValueCents = useMemo(
    () => assets.reduce((sum, asset) => sum + (asset.estimated_value_cents ?? 0), 0),
    [assets],
  );

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (asset: Asset) => {
    setEditing(asset);
    setFormError(null);
    setFormOpen(true);
  };

  const handleFormSubmit = (input: AssetInput | AssetPatchInput) => {
    if (!householdId) return;
    setFormSaving(true);
    setFormError(null);
    // When editing is null the form always produces a full AssetInput.
    const request = editing
      ? api.updateAsset(editing.id, input)
      : api.createAsset(householdId, { ...(input as AssetInput), matter_id: matterId });
    request
      .then((saved) => {
        setFormOpen(false);
        setEditing(null);
        setNotice(editing ? `“${saved.label}” updated.` : `“${saved.label}” added.`);
        loadData(householdId);
      })
      .catch((err: unknown) =>
        setFormError(err instanceof Error ? err.message : 'Could not save the asset.'),
      )
      .finally(() => setFormSaving(false));
  };

  const openBeneficiaries = (asset: Asset) => {
    setBeneficiaryAsset(asset);
    setBeneficiariesError(null);
    setBeneficiariesOpen(true);
  };

  const handleSaveBeneficiaries = (designation: BeneficiaryDesignation) => {
    if (!beneficiaryAsset || !householdId) return;
    setBeneficiariesSaving(true);
    setBeneficiariesError(null);
    api
      .updateAssetBeneficiaries(beneficiaryAsset.id, designation)
      .then((updated) => {
        setBeneficiariesOpen(false);
        setBeneficiaryAsset(null);
        setAssets((prev) => prev.map((asset) => (asset.id === updated.id ? updated : asset)));
        setNotice('Beneficiaries saved.');
      })
      .catch((err: unknown) =>
        setBeneficiariesError(
          err instanceof Error ? err.message : 'Could not save the beneficiaries.',
        ),
      )
      .finally(() => setBeneficiariesSaving(false));
  };

  // Confirmation goes through the in-app Modal, never window.confirm:
  // browsing contexts that suppress native dialogs make confirm() return
  // false without showing anything, so the click silently did nothing.
  const handleDelete = (asset: Asset) => {
    setDeleteTarget(asset);
  };

  const confirmDelete = () => {
    const asset = deleteTarget;
    if (!asset || !householdId) return;
    setDeletingId(asset.id);
    setNotice(null);
    api
      .deleteAsset(asset.id)
      .then(() => {
        setNotice(`“${asset.label}” deleted.`);
        setDeleteTarget(null);
        loadData(householdId);
      })
      .catch((err: unknown) => {
        setNotice(
          err instanceof Error ? `Could not delete: ${err.message}` : 'Could not delete the asset.',
        );
        setDeleteTarget(null);
      })
      .finally(() => setDeletingId(null));
  };

  const handleEvaluate = (asset: Asset) => {
    setEvaluatingId(asset.id);
    setNotice(null);
    api
      .evaluateAssetAlignment(asset.id)
      .then((result) => {
        setFlagsByAsset((prev) => ({ ...prev, [asset.id]: result.flags }));
        setNotice(
          result.flags.length === 0
            ? `“${asset.label}” looks aligned — no flags.`
            : `“${asset.label}” has ${result.flags.length} ${result.flags.length === 1 ? 'flag' : 'flags'} for your attorney to review.`,
        );
      })
      .catch((err: unknown) =>
        setNotice(
          err instanceof Error
            ? `Could not check alignment: ${err.message}`
            : 'Could not check alignment.',
        ),
      )
      .finally(() => setEvaluatingId(null));
  };

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Assets</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Everything your plan covers — what it&apos;s worth, who receives it, and any issues your
          attorney should see. Nothing here is final until your attorney approves it.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your assets will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {householdId && loading && <LoadingState lines={4} label="Loading assets…" />}
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
        <section aria-label="Assets" className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-lf-text-muted">
              {assets.length === 0
                ? 'No assets listed yet.'
                : `${assets.length} ${assets.length === 1 ? 'asset' : 'assets'} · total ${formatMoney(totalValueCents)}`}
            </p>
            <Button type="button" size="sm" onClick={openCreate}>
              Add asset
            </Button>
          </div>

          {assets.length === 0 ? (
            <EmptyState
              title="No assets yet"
              description="Add your home, accounts, vehicles, and other property. You will name beneficiaries for each one, and we will flag anything that looks misaligned."
              action={
                <Button type="button" onClick={openCreate}>
                  Add your first asset
                </Button>
              }
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {assets.map((asset) => (
                <AssetCard
                  key={asset.id}
                  asset={asset}
                  flags={flagsByAsset[asset.id] ?? []}
                  evaluating={evaluatingId === asset.id}
                  deleting={deletingId === asset.id}
                  onEdit={openEdit}
                  onBeneficiaries={openBeneficiaries}
                  onDelete={handleDelete}
                  onEvaluate={handleEvaluate}
                />
              ))}
            </div>
          )}
        </section>
      )}

      <AssetForm
        key={editing ? `edit-${editing.id}` : 'new'}
        open={formOpen}
        asset={editing}
        saving={formSaving}
        formError={formError}
        onSubmit={handleFormSubmit}
        onClose={() => {
          setFormOpen(false);
          setEditing(null);
        }}
      />

      <Modal
        open={deleteTarget != null}
        onClose={() => setDeleteTarget(null)}
        title="Delete this asset?"
        description={
          deleteTarget
            ? `“${deleteTarget.label}” will be removed from your plan, along with its beneficiary designations. This cannot be undone.`
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
            {deleteTarget != null && deletingId === deleteTarget.id ? 'Deleting…' : 'Delete asset'}
          </Button>
        </div>
      </Modal>

      {beneficiaryAsset && (
        <BeneficiaryEditor
          key={beneficiaryAsset.id}
          open={beneficiariesOpen}
          asset={beneficiaryAsset}
          saving={beneficiariesSaving}
          formError={beneficiariesError}
          onSave={handleSaveBeneficiaries}
          onClose={() => {
            setBeneficiariesOpen(false);
            setBeneficiaryAsset(null);
          }}
        />
      )}
    </div>
  );
}
