/** @vitest-environment jsdom */
/**
 * Stage F / F3 follow-up: team-settings revokeInvitation.
 *
 * trackSave now clears the header save indicator silently on a 4xx
 * rejection (the calling surface owns the truthful message) — but this
 * call site had NO local error surface at all: handleRevoke had no
 * .catch, so a failed revoke showed the user nothing. It must surface
 * its own inline error for ANY failure (4xx, 5xx, network), and the
 * success path must stay exactly as before.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/api-client', () => {
  class MockApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
    get isPermissionDenied(): boolean {
      return this.status === 403;
    }
  }
  return {
    ApiError: MockApiError,
    adminApi: {
      getCurrentFirm: vi.fn(),
      listFirmMembers: vi.fn(),
      listFeatureFlags: vi.fn(),
      setFeatureFlag: vi.fn(),
    },
    firmApi: {
      listInvitations: vi.fn(),
      createInvitation: vi.fn(),
      revokeInvitation: vi.fn(),
    },
  };
});

import FirmTeamSettingsPage from '../app/(app)/firm/team-settings/page';
import { adminApi, firmApi, ApiError } from '@/lib/api-client';

const getCurrentFirm = vi.mocked(adminApi.getCurrentFirm);
const listFirmMembers = vi.mocked(adminApi.listFirmMembers);
const listFeatureFlags = vi.mocked(adminApi.listFeatureFlags);
const listInvitations = vi.mocked(firmApi.listInvitations);
const revokeInvitation = vi.mocked(firmApi.revokeInvitation);

const pendingInvitation = {
  id: 'inv-1',
  firm_id: 'firm-1',
  email: 'newhire@example.com',
  role: 'firm_staff',
  household_id: null,
  status: 'pending',
  expires_at: '2026-11-25T12:00:00Z',
  accepted_user_id: null,
  created_at: '2026-10-26T12:00:00Z',
};

describe('FirmTeamSettingsPage revoke invitation (Stage F F3 follow-up)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCurrentFirm.mockResolvedValue({
      id: 'firm-1',
      legal_name: 'QA Law LLC',
      display_name: 'QA Law',
      status: 'active',
      created_at: '2026-01-01T12:00:00Z',
    });
    listFirmMembers.mockResolvedValue([]);
    listFeatureFlags.mockResolvedValue([]);
    listInvitations.mockResolvedValue([pendingInvitation]);
  });

  it('shows a user-visible error when revoke is rejected with a 4xx', async () => {
    const user = userEvent.setup();
    revokeInvitation.mockRejectedValue(
      new ApiError(403, 'Only firm admins can revoke invitations.'),
    );
    render(<FirmTeamSettingsPage />);

    await user.click(await screen.findByRole('button', { name: 'Revoke' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Only firm admins can revoke invitations.');
    // The invitation is still pending — the Revoke action stays available.
    expect(screen.getByRole('button', { name: 'Revoke' })).toBeDefined();
    expect(screen.getByText('Pending')).toBeDefined();
  });

  it('shows a user-visible error when revoke fails with a 5xx / network failure', async () => {
    const user = userEvent.setup();
    revokeInvitation.mockRejectedValue(new TypeError('Failed to fetch'));
    render(<FirmTeamSettingsPage />);

    await user.click(await screen.findByRole('button', { name: 'Revoke' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).not.toBe('');
    expect(screen.getByRole('button', { name: 'Revoke' })).toBeDefined();
  });

  it('success path unchanged: a resolved revoke flips the row to Revoked with no error', async () => {
    const user = userEvent.setup();
    revokeInvitation.mockResolvedValue({ ...pendingInvitation, status: 'revoked' });
    render(<FirmTeamSettingsPage />);

    await user.click(await screen.findByRole('button', { name: 'Revoke' }));

    await waitFor(() => expect(revokeInvitation).toHaveBeenCalledWith('inv-1'));
    expect(await screen.findByText('Revoked')).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Revoke' })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
