'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Modal } from '@/components/ui/modal';
import type { Asset, AssetInput, AssetPatchInput } from '@/lib/api-client';

export const ASSET_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'bank_account', label: 'Bank account' },
  { value: 'investment', label: 'Investment' },
  { value: 'retirement', label: 'Retirement account' },
  { value: 'real_estate', label: 'Real estate' },
  { value: 'business', label: 'Business interest' },
  { value: 'insurance', label: 'Life insurance' },
  { value: 'vehicle', label: 'Vehicle' },
  { value: 'other', label: 'Other' },
];

export const OWNERSHIP_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'sole', label: 'Sole ownership' },
  { value: 'joint', label: 'Joint ownership' },
  { value: 'tenants_in_common', label: 'Tenants in common' },
  { value: 'community_property', label: 'Community property' },
  { value: 'trust_owned', label: 'Owned by a trust' },
  { value: 'other', label: 'Other' },
];

export function assetTypeLabel(assetType: string): string {
  return ASSET_TYPE_OPTIONS.find((option) => option.value === assetType)?.label ?? assetType;
}

export function ownershipLabel(ownership: string | null): string {
  if (!ownership) return '—';
  return OWNERSHIP_OPTIONS.find((option) => option.value === ownership)?.label ?? ownership;
}

interface AssetFormProps {
  open: boolean;
  asset: Asset | null;
  saving: boolean;
  formError: string | null;
  onSubmit: (input: AssetInput | AssetPatchInput) => void;
  onClose: () => void;
}

/** Create/edit asset form shown inside a modal. Money is entered in dollars. */
export function AssetForm({ open, asset, saving, formError, onSubmit, onClose }: AssetFormProps) {
  const [assetType, setAssetType] = useState(asset?.asset_type ?? 'real_estate');
  const [label, setLabel] = useState(asset?.label ?? '');
  const [valueDollars, setValueDollars] = useState(
    asset?.estimated_value_cents != null ? String(asset.estimated_value_cents / 100) : '',
  );
  const [ownership, setOwnership] = useState(asset?.ownership ?? 'other');
  const [notes, setNotes] = useState(asset?.notes ?? '');
  const [validationError, setValidationError] = useState<string | null>(null);

  const prefix = asset ? 'edit-asset' : 'new-asset';
  const selectClassName =
    'h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500';

  const parseValue = (): number | null | 'invalid' => {
    const trimmed = valueDollars.trim();
    if (trimmed.length === 0) return null;
    const parsed = Number.parseFloat(trimmed);
    if (!Number.isFinite(parsed) || parsed < 0) return 'invalid';
    return Math.round(parsed * 100);
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmedLabel = label.trim();
    if (trimmedLabel.length === 0) {
      setValidationError('Give the asset a name.');
      return;
    }
    const estimatedValueCents = parseValue();
    if (estimatedValueCents === 'invalid') {
      setValidationError('Estimated value must be a positive number.');
      return;
    }
    if (!asset && estimatedValueCents === null) {
      setValidationError('Add an estimated value so your attorney can plan around it.');
      return;
    }
    setValidationError(null);
    const trimmedNotes = notes.trim() || null;
    if (asset) {
      const patch: AssetPatchInput = {
        asset_type: assetType,
        label: trimmedLabel,
        ownership,
        notes: trimmedNotes,
      };
      if (estimatedValueCents !== null) {
        patch.estimated_value_cents = estimatedValueCents;
      }
      onSubmit(patch);
    } else {
      onSubmit({
        asset_type: assetType,
        label: trimmedLabel,
        estimated_value_cents: estimatedValueCents ?? 0,
        ownership,
        notes: trimmedNotes,
      });
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={asset ? 'Edit asset' : 'Add an asset'}
      description="Describe what you own. Your attorney reviews everything before it is finalized."
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-type`}>Type</Label>
            <select
              id={`${prefix}-type`}
              value={assetType}
              onChange={(event) => setAssetType(event.target.value)}
              className={selectClassName}
            >
              {ASSET_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-label`}>Name</Label>
            <Input
              id={`${prefix}-label`}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="e.g. Maple Street house"
              maxLength={200}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-value`}>Estimated value (USD)</Label>
            <Input
              id={`${prefix}-value`}
              type="number"
              min={0}
              step="0.01"
              value={valueDollars}
              onChange={(event) => setValueDollars(event.target.value)}
              placeholder="0.00"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-ownership`}>Ownership</Label>
            <select
              id={`${prefix}-ownership`}
              value={ownership}
              onChange={(event) => setOwnership(event.target.value)}
              className={selectClassName}
            >
              {OWNERSHIP_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-notes`}>Notes</Label>
          <textarea
            id={`${prefix}-notes`}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            rows={2}
            maxLength={2000}
            placeholder="Optional — anything your attorney should know"
            className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          />
        </div>
        {(validationError ?? formError) && (
          <p role="alert" className="text-sm text-lf-error-700">
            {validationError ?? formError}
          </p>
        )}
        <div className="flex gap-3">
          <Button type="submit" disabled={saving}>
            {saving ? 'Saving…' : asset ? 'Save changes' : 'Add asset'}
          </Button>
          <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
        </div>
      </form>
    </Modal>
  );
}
