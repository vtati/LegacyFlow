'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Modal } from '@/components/ui/modal';
import type { Asset, BeneficiaryDesignation, BeneficiaryEntry } from '@/lib/api-client';

interface BeneficiaryEditorProps {
  open: boolean;
  asset: Asset;
  saving: boolean;
  formError: string | null;
  onSave: (designation: BeneficiaryDesignation) => void;
  onClose: () => void;
}

type Tier = 'primary' | 'contingent';

const TIER_LABELS: Record<Tier, string> = {
  primary: 'Primary',
  contingent: 'Contingent',
};

const selectClassName =
  'h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500';

function TierList({
  title,
  entries,
  onRemove,
}: {
  title: string;
  entries: BeneficiaryEntry[];
  onRemove: (index: number) => void;
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-lf-text">{title}</h3>
      {entries.length === 0 ? (
        <p className="mt-1 text-sm text-lf-text-subtle">None listed.</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {entries.map((entry, index) => (
            <li
              key={`${entry.name}-${index}`}
              className="flex items-center justify-between gap-3 rounded-lf-control border border-lf-border px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-lf-text">{entry.name}</p>
                {entry.relationship && (
                  <p className="text-xs text-lf-text-subtle">{entry.relationship}</p>
                )}
              </div>
              <Button type="button" variant="outline" size="sm" onClick={() => onRemove(index)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Beneficiary editor: named primary and contingent lists plus an add form.
 * The draft is kept locally until the family saves the whole designation.
 */
export function BeneficiaryEditor({
  open,
  asset,
  saving,
  formError,
  onSave,
  onClose,
}: BeneficiaryEditorProps) {
  const raw = asset.beneficiary_designation;
  const initial = {
    primary: Array.isArray(raw?.primary) ? raw.primary : [],
    contingent: Array.isArray(raw?.contingent) ? raw.contingent : [],
  };
  const [primary, setPrimary] = useState<BeneficiaryEntry[]>(initial.primary);
  const [contingent, setContingent] = useState<BeneficiaryEntry[]>(initial.contingent);
  const [name, setName] = useState('');
  const [relationship, setRelationship] = useState('');
  const [tier, setTier] = useState<Tier>('primary');
  const [validationError, setValidationError] = useState<string | null>(null);

  const handleAdd = () => {
    const trimmedName = name.trim();
    if (trimmedName.length === 0) {
      setValidationError('Enter a name for the beneficiary.');
      return;
    }
    const entry: BeneficiaryEntry = {
      name: trimmedName,
      relationship: relationship.trim() || null,
    };
    setValidationError(null);
    if (tier === 'primary') {
      setPrimary((prev) => [...prev, entry]);
    } else {
      setContingent((prev) => [...prev, entry]);
    }
    setName('');
    setRelationship('');
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Beneficiaries — ${asset.label}`}
      description="Primary beneficiaries receive the asset first; contingent beneficiaries are backups. Save to apply your changes."
    >
      <div className="space-y-5">
        <TierList
          title="Primary beneficiaries"
          entries={primary}
          onRemove={(index) => setPrimary((prev) => prev.filter((_, i) => i !== index))}
        />
        <TierList
          title="Contingent beneficiaries"
          entries={contingent}
          onRemove={(index) => setContingent((prev) => prev.filter((_, i) => i !== index))}
        />

        <div className="space-y-3 rounded-lf-control border border-dashed border-lf-border p-4">
          <p className="text-sm font-semibold text-lf-text">Add a beneficiary</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="beneficiary-name">Name</Label>
              <Input
                id="beneficiary-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Jordan Lee"
                maxLength={200}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="beneficiary-relationship">Relationship</Label>
              <Input
                id="beneficiary-relationship"
                value={relationship}
                onChange={(event) => setRelationship(event.target.value)}
                placeholder="e.g. Spouse"
                maxLength={100}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="beneficiary-tier">Tier</Label>
              <select
                id="beneficiary-tier"
                value={tier}
                onChange={(event) => setTier(event.target.value as Tier)}
                className={selectClassName}
              >
                {(Object.keys(TIER_LABELS) as Tier[]).map((value) => (
                  <option key={value} value={value}>
                    {TIER_LABELS[value]}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={handleAdd}>
            Add to list
          </Button>
        </div>

        {(validationError ?? formError) && (
          <p role="alert" className="text-sm text-lf-error-700">
            {validationError ?? formError}
          </p>
        )}

        <div className="flex gap-3">
          <Button type="button" disabled={saving} onClick={() => onSave({ primary, contingent })}>
            {saving ? 'Saving…' : 'Save beneficiaries'}
          </Button>
          <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
        </div>
      </div>
    </Modal>
  );
}
