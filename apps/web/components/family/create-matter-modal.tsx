'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Modal } from '@/components/ui/modal';
import { api, ApiError, type HouseholdCreateInput, type MatterCreateInput } from '@/lib/api-client';

const JURISDICTIONS = [
  { code: 'US-VA', label: 'Virginia' },
  { code: 'US-MD', label: 'Maryland' },
  { code: 'US-DC', label: 'District of Columbia' },
  { code: 'US-NC', label: 'North Carolina' },
] as const;

const PLAN_TYPES = [
  { value: 'will', label: 'Last Will and Testament' },
  { value: 'trust', label: 'Revocable Living Trust' },
  { value: 'poa', label: 'Power of Attorney' },
  { value: 'bundle', label: 'Complete Estate Plan Bundle' },
] as const;

interface CreateMatterModalProps {
  open: boolean;
  onClose: () => void;
  householdId: string | null;
  householdName: string | null;
  onCreated: () => void;
}

/**
 * Modal for starting a new estate plan (matter).
 * If the user has no household, one is created first.
 */
export function CreateMatterModal({
  open,
  onClose,
  householdId,
  householdName,
  onCreated,
}: CreateMatterModalProps): React.JSX.Element {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [jurisdiction, setJurisdiction] = useState<string>('US-VA');
  const [planType, setPlanType] = useState<string>('will');
  const [householdNameInput, setHouseholdNameInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsHousehold = householdId == null;

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      let hid = householdId;
      // Create household if the user doesn't have one
      if (hid == null) {
        const name = householdNameInput.trim() || 'My Household';
        const householdInput: HouseholdCreateInput = { name };
        const household = await api.createHousehold(householdInput);
        hid = household.id;
      }
      const matterInput: MatterCreateInput = {
        household_id: hid,
        jurisdiction_code: jurisdiction,
        title: title.trim() || undefined,
        plan_type: planType,
      };
      const matter = await api.createMatter(matterInput);
      onCreated();
      onClose();
      router.push(`/family/interview/${matter.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError(
          'Your account does not have permission to create estate plans yet. Please contact your firm to set up your household.',
        );
      } else {
        setError(
          err instanceof ApiError ? err.message : 'Something went wrong. Please try again.',
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Start new estate plan">
      <form onSubmit={handleSubmit} className="space-y-5">
        {needsHousehold && (
          <div className="space-y-2">
            <Label htmlFor="household-name">Household name</Label>
            <Input
              id="household-name"
              value={householdNameInput}
              onChange={(e) => setHouseholdNameInput(e.target.value)}
              placeholder="e.g. The Sharma Family"
              disabled={submitting}
            />
          </div>
        )}
        {!needsHousehold && householdName && (
          <p className="text-sm text-lf-text-muted">
            Creating for household: <span className="font-medium text-lf-text">{householdName}</span>
          </p>
        )}
        <div className="space-y-2">
          <Label htmlFor="matter-title">Plan title</Label>
          <Input
            id="matter-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Sharma Family Estate Plan"
            disabled={submitting}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="jurisdiction">Jurisdiction</Label>
          <select
            id="jurisdiction"
            value={jurisdiction}
            onChange={(e) => setJurisdiction(e.target.value)}
            disabled={submitting}
            className="w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2.5 text-sm text-lf-text focus:border-lf-primary-500 focus:outline-none focus:ring-2 focus:ring-lf-primary-500/20"
          >
            {JURISDICTIONS.map((j) => (
              <option key={j.code} value={j.code}>
                {j.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="plan-type">Plan type</Label>
          <select
            id="plan-type"
            value={planType}
            onChange={(e) => setPlanType(e.target.value)}
            disabled={submitting}
            className="w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2.5 text-sm text-lf-text focus:border-lf-primary-500 focus:outline-none focus:ring-2 focus:ring-lf-primary-500/20"
          >
            {PLAN_TYPES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        {error && (
          <p className="rounded-lf-control bg-lf-error-50 px-3 py-2 text-sm text-lf-error-700" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-3">
          <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting} className="lf-pressable">
            {submitting ? 'Creating…' : 'Start estate plan'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
