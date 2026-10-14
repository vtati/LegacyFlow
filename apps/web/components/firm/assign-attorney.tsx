'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { adminApi, firmApi, type FirmMember } from '@/lib/api-client';

/**
 * Assign-attorney control for the firm matter detail header.
 * Lists active attorneys in the firm; assigning sets the matter's
 * assigned_attorney_id via PATCH /matters/{id}/assign.
 */
export function AssignAttorney({
  matterId,
  currentAttorneyUserId,
  onAssigned,
}: {
  matterId: string;
  currentAttorneyUserId: string | null;
  onAssigned: (attorneyUserId: string | null) => void;
}): React.JSX.Element {
  const [attorneys, setAttorneys] = useState<FirmMember[]>([]);
  const [selected, setSelected] = useState<string>(currentAttorneyUserId ?? '');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi
      .listFirmMembers()
      .then((members) =>
        setAttorneys(
          members.filter((m) => m.role === 'attorney' && m.status === 'active'),
        ),
      )
      .catch(() => setAttorneys([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    setSelected(currentAttorneyUserId ?? '');
  }, [currentAttorneyUserId]);

  const handleSave = async (): Promise<void> => {
    setError(null);
    setSaving(true);
    try {
      await firmApi.assignMatterAttorney(matterId, selected || null);
      onAssigned(selected || null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not assign attorney.');
    } finally {
      setSaving(false);
    }
  };

  const dirty = (selected || null) !== currentAttorneyUserId;

  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="space-y-1">
        <Label htmlFor="assign-attorney">Assigned attorney</Label>
        <select
          id="assign-attorney"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          disabled={loading || saving}
          className="flex min-w-[200px] rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 disabled:opacity-60"
        >
          <option value="">Unassigned</option>
          {attorneys.map((a) => (
            <option key={a.user_id} value={a.user_id}>
              {a.display_name || a.email || a.user_id}
            </option>
          ))}
        </select>
      </div>
      <Button type="button" size="sm" disabled={!dirty || saving} onClick={handleSave}>
        {saving ? 'Saving…' : 'Assign'}
      </Button>
      {error && (
        <p role="alert" className="w-full text-sm text-lf-error-700">
          {error}
        </p>
      )}
    </div>
  );
}
