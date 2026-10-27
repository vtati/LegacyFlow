/** @vitest-environment jsdom */
/**
 * R2 (i): document_release gate has NO UI path.
 *
 * ReviewDecisionPanel only created/decided an intake_approval gate, and it
 * rendered nothing actionable once the matter reached APPROVED_FOR_ASSEMBLY —
 * exactly the state a document_release gate must be created/decided in
 * (API: GATE_FROM_STATE document_release = APPROVED_FOR_ASSEMBLY). The panel
 * must now offer create+approve for document_release in that state,
 * mirroring the intake flow, so release-production can succeed entirely
 * from the UI.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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
      getMatter: vi.fn(),
      createClarification: vi.fn(),
      transitionMatter: vi.fn(),
    },
  };
});

import { ReviewDecisionPanel } from '../components/firm/review-decision-panel';
import { firmApi } from '@/lib/api-client';

const listGates = vi.mocked(firmApi.listGates);
const createGate = vi.mocked(firmApi.createGate);
const decideGate = vi.mocked(firmApi.decideGate);
const getMatter = vi.mocked(firmApi.getMatter);

const createdGate = {
  id: 'gate-release-1',
  firm_id: 'firm-1',
  matter_id: 'matter-1',
  gate_type: 'document_release',
  status: 'pending' as const,
  decided_by_user_id: null,
  decided_at: null,
  notes: null,
  created_at: '2026-10-27T12:00:00Z',
  updated_at: '2026-10-27T12:00:00Z',
};

describe('ReviewDecisionPanel document release (R2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getMatter.mockResolvedValue({
      matter: { status: 'DOCUMENTS_RELEASED' },
    } as Awaited<ReturnType<typeof getMatter>>);
    decideGate.mockResolvedValue({ ...createdGate, status: 'approved' });
  });

  it('creates and approves a document_release gate when none exists', async () => {
    const user = userEvent.setup();
    const onStatusChange = vi.fn();
    listGates.mockResolvedValue([]);
    createGate.mockResolvedValue(createdGate);

    render(
      <ReviewDecisionPanel
        matterId="matter-1"
        status="APPROVED_FOR_ASSEMBLY"
        onStatusChange={onStatusChange}
      />,
    );

    await user.click(
      await screen.findByRole('button', { name: /approve document release/i }),
    );

    expect(createGate).toHaveBeenCalledWith('matter-1', {
      gate_type: 'document_release',
      notes: null,
    });
    expect(decideGate).toHaveBeenCalledWith('gate-release-1', {
      decision: 'approved',
      notes: null,
    });
    expect(onStatusChange).toHaveBeenCalledWith('DOCUMENTS_RELEASED');
  });

  it('decides an existing pending document_release gate without creating one', async () => {
    const user = userEvent.setup();
    listGates.mockResolvedValue([createdGate]);

    render(
      <ReviewDecisionPanel
        matterId="matter-1"
        status="APPROVED_FOR_ASSEMBLY"
        onStatusChange={vi.fn()}
      />,
    );

    await user.click(
      await screen.findByRole('button', { name: /approve document release/i }),
    );

    expect(createGate).not.toHaveBeenCalled();
    expect(decideGate).toHaveBeenCalledWith('gate-release-1', {
      decision: 'approved',
      notes: null,
    });
  });

  it('surfaces an error when gate creation fails (e.g. wrong matter state)', async () => {
    const user = userEvent.setup();
    listGates.mockResolvedValue([]);
    createGate.mockRejectedValue(new Error('A document_release gate requires APPROVED_FOR_ASSEMBLY.'));

    render(
      <ReviewDecisionPanel
        matterId="matter-1"
        status="APPROVED_FOR_ASSEMBLY"
        onStatusChange={vi.fn()}
      />,
    );

    await user.click(
      await screen.findByRole('button', { name: /approve document release/i }),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'A document_release gate requires APPROVED_FOR_ASSEMBLY.',
    );
    expect(decideGate).not.toHaveBeenCalled();
  });
});
