/** @vitest-environment jsdom */
/**
 * Family vault page — share link + revoke (Phase 3 QA defect 1).
 *
 * (a) The "save this link" banner must render the token the API actually
 * returns in the create response (`token`), not `undefined`.
 * (b) Revoke must not depend on `window.confirm`: in browsing contexts
 * that suppress native dialogs the confirm silently returns false and
 * the click does nothing at all — no dialog, no error, no state change.
 * The page confirms through the in-app Modal instead.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const activeShare = {
  id: 'share-1',
  firm_id: 'firm-1',
  matter_id: 'matter-1',
  label: 'For Ana',
  expires_at: null,
  revoked_at: null,
  created_at: '2026-10-20T12:00:00Z',
};

const revokeShare = vi.fn(async (id: string) => ({
  ...activeShare,
  id,
  revoked_at: '2026-10-26T12:00:00Z',
}));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    listShares: vi.fn(async () => [activeShare]),
    createShare: vi.fn(async () => ({
      ...activeShare,
      id: 'share-2',
      label: 'For Ben',
      token: 'raw-token-abc123',
    })),
    revokeShare: (id: string) => revokeShare(id),
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
  formatStatus: (status: string) => status,
}));

import FamilyVaultPage from '../app/(app)/family/vault/page';

describe('FamilyVaultPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // The QA browsing context suppresses native dialogs: confirm()
    // never shows UI and returns false.
    vi.spyOn(window, 'confirm').mockReturnValue(false);
  });

  it('revokes through the in-app dialog even when window.confirm is suppressed', async () => {
    const user = userEvent.setup();
    render(<FamilyVaultPage />);

    const revokeButton = await screen.findByRole('button', { name: /revoke/i });
    await user.click(revokeButton);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/For Ana/)).toBeDefined();
    expect(window.confirm).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: /revoke share/i }));
    await waitFor(() => expect(revokeShare).toHaveBeenCalledWith('share-1'));
    expect(await screen.findByText('Revoked')).toBeDefined();
    expect(screen.queryByRole('button', { name: /^revoke$/i })).toBeNull();
  });

  it('renders the created share link with the API-returned token', async () => {
    const user = userEvent.setup();
    render(<FamilyVaultPage />);

    const labelInput = await screen.findByLabelText(/label/i);
    await user.type(labelInput, 'For Ben');
    await user.click(screen.getByRole('button', { name: /create share/i }));

    const banner = await screen.findByLabelText('New vault share');
    expect(within(banner).getByText(/\/vault\/raw-token-abc123/)).toBeDefined();
    expect(within(banner).queryByText(/undefined/)).toBeNull();
  });
});
