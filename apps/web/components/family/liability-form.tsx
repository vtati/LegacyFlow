'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Modal } from '@/components/ui/modal';
import type { Liability, LiabilityInput, LiabilityPatchInput } from '@/lib/api-client';

export const LIABILITY_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'mortgage', label: 'Mortgage' },
  { value: 'loan', label: 'Loan' },
  { value: 'credit_card', label: 'Credit card' },
  { value: 'other', label: 'Other' },
];

export function liabilityTypeLabel(liabilityType: string): string {
  return (
    LIABILITY_TYPE_OPTIONS.find((option) => option.value === liabilityType)?.label ?? liabilityType
  );
}

interface LiabilityFormProps {
  open: boolean;
  liability: Liability | null;
  saving: boolean;
  formError: string | null;
  onSubmit: (input: LiabilityInput | LiabilityPatchInput) => void;
  onClose: () => void;
}

/** Create/edit liability form shown inside a modal. Money is entered in dollars. */
export function LiabilityForm({
  open,
  liability,
  saving,
  formError,
  onSubmit,
  onClose,
}: LiabilityFormProps) {
  const [liabilityType, setLiabilityType] = useState(liability?.liability_type ?? 'mortgage');
  const [label, setLabel] = useState(liability?.label ?? '');
  const [balanceDollars, setBalanceDollars] = useState(
    liability?.balance_cents != null ? String(liability.balance_cents / 100) : '',
  );
  const [paymentDollars, setPaymentDollars] = useState(
    liability?.monthly_payment_cents != null ? String(liability.monthly_payment_cents / 100) : '',
  );
  const [notes, setNotes] = useState(liability?.notes ?? '');
  const [validationError, setValidationError] = useState<string | null>(null);

  const prefix = liability ? 'edit-liability' : 'new-liability';
  const selectClassName =
    'h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500';

  const parseMoney = (raw: string): number | null | 'invalid' => {
    const trimmed = raw.trim();
    if (trimmed.length === 0) return null;
    const parsed = Number.parseFloat(trimmed);
    if (!Number.isFinite(parsed) || parsed < 0) return 'invalid';
    return Math.round(parsed * 100);
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmedLabel = label.trim();
    if (trimmedLabel.length === 0) {
      setValidationError('Give the liability a name.');
      return;
    }
    const balanceCents = parseMoney(balanceDollars);
    if (balanceCents === 'invalid') {
      setValidationError('Balance must be a positive number.');
      return;
    }
    if (!liability && balanceCents === null) {
      setValidationError('Add the current balance so your attorney can plan around it.');
      return;
    }
    const paymentCents = parseMoney(paymentDollars);
    if (paymentCents === 'invalid') {
      setValidationError('Monthly payment must be a positive number (or leave it blank).');
      return;
    }
    setValidationError(null);
    const trimmedNotes = notes.trim() || null;
    if (liability) {
      const patch: LiabilityPatchInput = {
        liability_type: liabilityType,
        label: trimmedLabel,
        monthly_payment_cents: paymentCents,
        notes: trimmedNotes,
      };
      if (balanceCents !== null) {
        patch.balance_cents = balanceCents;
      }
      onSubmit(patch);
    } else {
      onSubmit({
        liability_type: liabilityType,
        label: trimmedLabel,
        balance_cents: balanceCents ?? 0,
        monthly_payment_cents: paymentCents,
        notes: trimmedNotes,
      });
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={liability ? 'Edit liability' : 'Add a liability'}
      description="List what you owe so your plan reflects the full picture. Your attorney reviews everything."
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-type`}>Type</Label>
            <select
              id={`${prefix}-type`}
              value={liabilityType}
              onChange={(event) => setLiabilityType(event.target.value)}
              className={selectClassName}
            >
              {LIABILITY_TYPE_OPTIONS.map((option) => (
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
              placeholder="e.g. Maple Street mortgage"
              maxLength={200}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-balance`}>Balance (USD)</Label>
            <Input
              id={`${prefix}-balance`}
              type="number"
              min={0}
              step="0.01"
              value={balanceDollars}
              onChange={(event) => setBalanceDollars(event.target.value)}
              placeholder="0.00"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-payment`}>Monthly payment (USD)</Label>
            <Input
              id={`${prefix}-payment`}
              type="number"
              min={0}
              step="0.01"
              value={paymentDollars}
              onChange={(event) => setPaymentDollars(event.target.value)}
              placeholder="0.00"
            />
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
            placeholder="Optional — lender, account details, anything your attorney should know"
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
            {saving ? 'Saving…' : liability ? 'Save changes' : 'Add liability'}
          </Button>
          <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
        </div>
      </form>
    </Modal>
  );
}
