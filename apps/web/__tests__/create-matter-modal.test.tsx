/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CreateMatterModal } from '../components/family/create-matter-modal';
import { api, ApiError } from '../lib/api-client';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('../lib/api-client', () => {
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
    api: {
      createHousehold: vi.fn(),
      createMatter: vi.fn(),
    },
  };
});

const createMatter = vi.mocked(api.createMatter);

function renderModal(): void {
  render(
    <CreateMatterModal
      open
      onClose={vi.fn()}
      householdId="household-1"
      householdName="The QA Family"
      onCreated={vi.fn()}
    />,
  );
}

describe('CreateMatterModal error presentation (F3)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the permission message in-form, exactly once, on a 403', async () => {
    const user = userEvent.setup();
    createMatter.mockRejectedValue(
      new ApiError(403, 'Forbidden: no firm membership for this account.'),
    );
    renderModal();
    await user.click(screen.getByRole('button', { name: 'Start estate plan' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      'Your account does not have permission to create estate plans yet. Please contact your firm to set up your household.',
    );
    // One truthful message: the server detail is not repeated as a second error.
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(alert.textContent).not.toContain('Forbidden');
  });

  it('shows the server message in-form for a non-403 ApiError', async () => {
    const user = userEvent.setup();
    createMatter.mockRejectedValue(new ApiError(500, 'Matter service unavailable.'));
    renderModal();
    await user.click(screen.getByRole('button', { name: 'Start estate plan' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Matter service unavailable.');
  });
});
