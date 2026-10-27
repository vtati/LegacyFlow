/** @vitest-environment jsdom */
/**
 * R2 (i): GatesTab only decided gates that already existed — nothing in the
 * UI could CREATE a document_release gate, so release-production 409'd.
 * The tab must offer creation when no pending/approved document_release
 * gate exists (in the state the API allows it), after which the new
 * pending gate is decidable in the same tab.
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
    firmApi: {
      listGates: vi.fn(),
      createGate: vi.fn(),
      decideGate: vi.fn(),
    },
  };
});

import { GatesTab } from '../components/firm/gates-tab';
import { firmApi, type Gate } from '@/lib/api-client';

const listGates = vi.mocked(firmApi.listGates);
const createGate = vi.mocked(firmApi.createGate);
const decideGate = vi.mocked(firmApi.decideGate);

const pendingReleaseGate: Gate = {
  id: 'gate-release-1',
  firm_id: 'firm-1',
  matter_id: 'matter-1',
  gate_type: 'document_release',
  status: 'pending',
  decided_by_user_id: null,
  decided_at: null,
  notes: null,
  created_at: '2026-10-27T12:00:00Z',
  updated_at: '2026-10-27T12:00:00Z',
};

describe('GatesTab document_release creation (R2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('offers create for a missing document_release gate and posts the right payload', async () => {
    const user = userEvent.setup();
    listGates.mockResolvedValue([]);
    createGate.mockResolvedValue(pendingReleaseGate);

    render(<GatesTab matterId="matter-1" matterStatus="APPROVED_FOR_ASSEMBLY" />);

    await user.click(
      await screen.findByRole('button', { name: /create document release gate/i }),
    );

    await waitFor(() =>
      expect(createGate).toHaveBeenCalledWith('matter-1', {
        gate_type: 'document_release',
        notes: null,
      }),
    );
    // The newly created pending gate is immediately decidable in this tab.
    expect(await screen.findByText('Document Release')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDefined();
  });

  it('deciding the created gate calls the decision endpoint', async () => {
    const user = userEvent.setup();
    listGates.mockResolvedValue([pendingReleaseGate]);
    decideGate.mockResolvedValue({ ...pendingReleaseGate, status: 'approved' });

    render(<GatesTab matterId="matter-1" matterStatus="APPROVED_FOR_ASSEMBLY" />);

    // A pending gate already exists — no duplicate create affordance.
    expect(screen.queryByRole('button', { name: /create document release gate/i })).toBeNull();

    await user.click(await screen.findByRole('button', { name: 'Approve' }));

    await waitFor(() =>
      expect(decideGate).toHaveBeenCalledWith('gate-release-1', {
        decision: 'approved',
        notes: null,
      }),
    );
    expect(createGate).not.toHaveBeenCalled();
  });

  it('does not offer creation once a document_release gate is approved', async () => {
    listGates.mockResolvedValue([{ ...pendingReleaseGate, status: 'approved' }]);

    render(<GatesTab matterId="matter-1" matterStatus="DOCUMENTS_RELEASED" />);

    expect(await screen.findByText('Document Release')).toBeDefined();
    expect(screen.queryByRole('button', { name: /create document release gate/i })).toBeNull();
  });

  it('invokes onMatterChanged after a successful gate decision so the parent can refetch', async () => {
    const user = userEvent.setup();
    const onMatterChanged = vi.fn();
    listGates.mockResolvedValue([pendingReleaseGate]);
    decideGate.mockResolvedValue({ ...pendingReleaseGate, status: 'approved' });

    render(
      <GatesTab
        matterId="matter-1"
        matterStatus="APPROVED_FOR_ASSEMBLY"
        onMatterChanged={onMatterChanged}
      />,
    );

    await user.click(await screen.findByRole('button', { name: 'Approve' }));

    await waitFor(() => expect(onMatterChanged).toHaveBeenCalledTimes(1));
  });

  it('does not offer creation when matterStatus is unknown (fail-closed)', async () => {
    listGates.mockResolvedValue([]);

    render(<GatesTab matterId="matter-1" />);

    expect(await screen.findByText('No approval gates')).toBeDefined();
    expect(screen.queryByRole('button', { name: /create document release gate/i })).toBeNull();
  });

  it('re-offers creation after a document_release gate was rejected, and re-create succeeds', async () => {
    const user = userEvent.setup();
    listGates.mockResolvedValue([{ ...pendingReleaseGate, status: 'rejected' }]);
    createGate.mockResolvedValue(pendingReleaseGate);

    render(<GatesTab matterId="matter-1" matterStatus="APPROVED_FOR_ASSEMBLY" />);

    // The rejected gate is shown, and the create card re-appears alongside it.
    expect(await screen.findByText('Document Release')).toBeDefined();
    await user.click(
      await screen.findByRole('button', { name: /create document release gate/i }),
    );

    await waitFor(() =>
      expect(createGate).toHaveBeenCalledWith('matter-1', {
        gate_type: 'document_release',
        notes: null,
      }),
    );
    // The fresh pending gate replaces the create affordance.
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /create document release gate/i }),
      ).toBeNull(),
    );
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDefined();
  });

  it('surfaces a create failure visibly instead of pretending nothing happened', async () => {
    const user = userEvent.setup();
    listGates.mockResolvedValue([]);
    createGate.mockRejectedValue(new Error('Gate requires APPROVED_FOR_ASSEMBLY.'));

    render(<GatesTab matterId="matter-1" matterStatus="APPROVED_FOR_ASSEMBLY" />);

    await user.click(
      await screen.findByRole('button', { name: /create document release gate/i }),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Gate requires APPROVED_FOR_ASSEMBLY.',
    );
  });
});
