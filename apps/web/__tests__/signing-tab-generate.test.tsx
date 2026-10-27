/** @vitest-environment jsdom */
/**
 * R2 (iii): signing checklist generation gap.
 *
 * Neither the firm Signing tab nor the family Signing page offered
 * checklist generation, although POST /v1/matters/{id}/signing-checklists
 * works (profile is resolved server-side from the matter's pack; the
 * optional body is only { document_type }). The firm tab must offer
 * "Generate signing checklist" when the matter's packages are all
 * released and no checklist exists yet, post the generation, and then
 * list the generated items.
 *
 * Also covers the firm client's checklist read: a 404 ("no checklist
 * yet") is an empty state, not an error — mirroring the family page.
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
      getChecklist: vi.fn(),
      generateChecklist: vi.fn(),
      listPackages: vi.fn(),
    },
  };
});

import { SigningTab } from '../components/firm/signing-tab';
import { firmApi, ApiError, type SigningChecklist } from '@/lib/api-client';

const getChecklist = vi.mocked(firmApi.getChecklist);
const generateChecklist = vi.mocked(firmApi.generateChecklist);
const listPackages = vi.mocked(firmApi.listPackages);

const generated: SigningChecklist = {
  id: 'checklist-1',
  matter_id: 'matter-1',
  execution_profile_id: 'VA-WILL-V0',
  document_type: 'will',
  requirements: { witnesses: { required_count: 2 }, notary: { legally_required: false } },
  items: [
    { key: 'written_document_prepared', label: 'Prepare the written document', detail: null },
    { key: 'testator_signature', label: 'Sign the document', detail: null },
  ],
  attestations: [],
  completed_at: null,
  created_at: '2026-10-27T12:00:00Z',
};

const releasedPkg = {
  id: 'pkg-1',
  firm_id: 'firm-1',
  matter_id: 'matter-1',
  template_id: 'tpl-1',
  status: 'released',
  created_at: '2026-10-27T12:00:00Z',
};

describe('SigningTab checklist generation (R2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('offers generation when documents are released and no checklist exists, then lists items', async () => {
    const user = userEvent.setup();
    getChecklist.mockRejectedValue(new ApiError(404, 'No signing checklist exists for this matter.'));
    listPackages.mockResolvedValue([releasedPkg]);
    generateChecklist.mockResolvedValue(generated);

    render(<SigningTab matterId="matter-1" />);

    await user.click(
      await screen.findByRole('button', { name: /generate signing checklist/i }),
    );

    // No document_type is forced: the API resolves the execution profile
    // (VA-WILL-V0) from the matter's pack when the body omits it.
    await waitFor(() => expect(generateChecklist).toHaveBeenCalledWith('matter-1'));
    expect(await screen.findByText('Prepare the written document')).toBeDefined();
    expect(screen.getByText('Sign the document')).toBeDefined();
    expect(screen.getByText(/VA-WILL-V0/)).toBeDefined();
    expect(screen.queryByRole('button', { name: /generate signing checklist/i })).toBeNull();
  });

  it('does not offer generation before the documents are released', async () => {
    getChecklist.mockRejectedValue(new ApiError(404, 'No signing checklist exists for this matter.'));
    listPackages.mockResolvedValue([{ ...releasedPkg, status: 'assembled' }]);

    render(<SigningTab matterId="matter-1" />);

    expect(await screen.findByText('No checklist')).toBeDefined();
    expect(screen.queryByRole('button', { name: /generate signing checklist/i })).toBeNull();
    expect(generateChecklist).not.toHaveBeenCalled();
  });

  it('renders an existing checklist without a generate action', async () => {
    getChecklist.mockResolvedValue(generated);
    listPackages.mockResolvedValue([releasedPkg]);

    render(<SigningTab matterId="matter-1" />);

    expect(await screen.findByText('Prepare the written document')).toBeDefined();
    expect(screen.queryByRole('button', { name: /generate signing checklist/i })).toBeNull();
  });

  it('surfaces a generation failure visibly', async () => {
    const user = userEvent.setup();
    getChecklist.mockRejectedValue(new ApiError(404, 'No signing checklist exists for this matter.'));
    listPackages.mockResolvedValue([releasedPkg]);
    generateChecklist.mockRejectedValue(new Error('Execution profile missing.'));

    render(<SigningTab matterId="matter-1" />);

    await user.click(
      await screen.findByRole('button', { name: /generate signing checklist/i }),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('Execution profile missing.');
  });
});
