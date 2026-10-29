/** @vitest-environment jsdom */
/**
 * UI-013: destructive family actions (delete asset, delete liability,
 * withdraw nomination) must confirm through the in-app Modal, never
 * `window.confirm` — dialog-suppressing browsing contexts make
 * confirm() silently return false, so the action did nothing at all.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const deleteAsset = vi.fn(async (_id: string) => undefined);
const deleteLiability = vi.fn(async (_id: string) => undefined);
const deleteNomination = vi.fn(async (_matterId: string, _id: string) => undefined);

const asset = {
  id: 'asset-1',
  firm_id: 'firm-1',
  household_id: 'hh-1',
  matter_id: 'matter-1',
  asset_type: 'bank_account',
  label: 'Joint savings',
  estimated_value_cents: 100000,
  ownership: 'joint',
  beneficiary_designation: { primary: [], contingent: [] },
  notes: null,
  created_at: '2026-10-20T12:00:00Z',
  updated_at: '2026-10-20T12:00:00Z',
};
const liability = {
  id: 'liab-1',
  firm_id: 'firm-1',
  household_id: 'hh-1',
  matter_id: 'matter-1',
  liability_type: 'mortgage',
  label: 'Home mortgage',
  balance_cents: 25000000,
  monthly_payment_cents: 180000,
  notes: null,
  created_at: '2026-10-20T12:00:00Z',
  updated_at: '2026-10-20T12:00:00Z',
};
const nomination = {
  id: 'nom-1',
  nomination_type: 'guardian',
  person_id: 'person-1',
  priority: 1,
  notes: null,
  status: 'active',
};

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    listAssets: vi.fn(async () => ({ items: [asset] })),
    deleteAsset: (id: string) => deleteAsset(id),
    listLiabilities: vi.fn(async () => ({ items: [liability] })),
    deleteLiability: (id: string) => deleteLiability(id),
    getHousehold: vi.fn(async () => ({
      household: { id: 'hh-1', name: 'Sharma household' },
      members: [],
      persons: [{ id: 'person-1', full_name: 'Ana Sharma', date_of_birth: null, email: null, kind: 'adult' }],
      relationships: null,
    })),
    listNominations: vi.fn(async (_matterId: string, type: string) =>
      type === 'guardian' ? [nomination] : [],
    ),
    deleteNomination: (matterId: string, id: string) => deleteNomination(matterId, id),
  },
}));

vi.mock('@/components/family/matter-context', () => ({
  useMatters: () => ({
    matters: [
      {
        id: 'matter-1',
        title: 'Sharma plan',
        status: 'intake_in_progress',
        jurisdiction_code: 'US-VA',
        completeness_pct: 9,
        household: { id: 'hh-1', name: 'Sharma household' },
      },
    ],
    loading: false,
    error: null,
    reload: vi.fn(),
  }),
  MatterSelect: () => null,
}));

import FamilyAssetsPage from '../app/(app)/family/assets/page';
import FamilyLiabilitiesPage from '../app/(app)/family/liabilities/page';
import FamilyGuardiansPage from '../app/(app)/family/guardians/page';

describe('destructive actions confirm via the in-app Modal (UI-013)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(false);
  });

  it('deletes an asset only after confirming in the modal; cancel keeps it', async () => {
    const user = userEvent.setup();
    render(<FamilyAssetsPage />);

    await user.click(await screen.findByRole('button', { name: /^delete$/i }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Joint savings/)).toBeDefined();
    expect(within(dialog).getByText(/cannot be undone/i)).toBeDefined();
    expect(window.confirm).not.toHaveBeenCalled();

    // Cancel: no delete call, dialog closes.
    await user.click(within(dialog).getByRole('button', { name: /cancel/i }));
    expect(deleteAsset).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // Confirm: delete runs.
    await user.click(screen.getByRole('button', { name: /^delete$/i }));
    const dialog2 = await screen.findByRole('dialog');
    await user.click(within(dialog2).getByRole('button', { name: /delete asset/i }));
    await waitFor(() => expect(deleteAsset).toHaveBeenCalledWith('asset-1'));
  });

  it('deletes a liability only after confirming in the modal', async () => {
    const user = userEvent.setup();
    render(<FamilyLiabilitiesPage />);

    await user.click(await screen.findByRole('button', { name: /^delete$/i }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Home mortgage/)).toBeDefined();
    expect(within(dialog).getByText(/cannot be undone/i)).toBeDefined();
    expect(window.confirm).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: /delete liability/i }));
    await waitFor(() => expect(deleteLiability).toHaveBeenCalledWith('liab-1'));
  });

  it('withdraws a nomination only after confirming in the modal; cancel keeps it', async () => {
    const user = userEvent.setup();
    render(<FamilyGuardiansPage />);

    await user.click(await screen.findByRole('button', { name: /^withdraw$/i }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Ana Sharma/)).toBeDefined();
    expect(window.confirm).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: /cancel/i }));
    expect(deleteNomination).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    await user.click(screen.getByRole('button', { name: /^withdraw$/i }));
    const dialog2 = await screen.findByRole('dialog');
    await user.click(within(dialog2).getByRole('button', { name: /withdraw nomination/i }));
    await waitFor(() => expect(deleteNomination).toHaveBeenCalledWith('matter-1', 'nom-1'));
  });
});
