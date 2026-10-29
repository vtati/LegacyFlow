/** @vitest-environment jsdom */
/**
 * Family profile add-person roles (FUNC-029 follow-through).
 *
 * The API guard on person creation (PersonCreate) only accepts the
 * non-privileged household roles `dependent` and `trusted_contact` for
 * auto-assigned membership: `primary_client` is a household singleton
 * and `collaborator` carries household write access, so both are minted
 * only by household provisioning / invitation flows and a create with
 * either now returns 422. The add-person select must therefore never
 * offer those two roles, its default must be an accepted role, and a
 * rejection from the endpoint must surface visibly in the form.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const matter = {
  id: 'matter-1',
  title: 'Sharma plan',
  status: 'intake_in_progress',
  jurisdiction_code: 'US-VA',
  completeness_pct: 9,
  household: { id: 'hh-1', name: 'Sharma household' },
};

const household = {
  household: { id: 'hh-1', name: 'Sharma household' },
  members: [],
  persons: [],
  relationships: null,
};

const hoisted = vi.hoisted(() => ({
  createPerson: vi.fn(),
}));
const { createPerson } = hoisted;

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    listMatters: vi.fn(async () => ({ items: [matter] })),
    getHousehold: vi.fn(async () => household),
    createPerson: hoisted.createPerson,
    updatePerson: vi.fn(async () => ({})),
  },
}));

import FamilyProfilePage from '../app/(app)/family/profile/page';

async function openAddForm(): Promise<HTMLSelectElement> {
  render(<FamilyProfilePage />);
  // Household loaded (empty member list) before the add form can open.
  expect(await screen.findByText('No members yet')).toBeDefined();
  fireEvent.click(screen.getByRole('button', { name: 'Add a person' }));
  return screen.getByLabelText('Relationship to household') as HTMLSelectElement;
}

describe('FamilyProfilePage add-person relationship roles', () => {
  beforeEach(() => {
    createPerson.mockReset();
    createPerson.mockResolvedValue({
      id: 'person-1',
      full_name: 'Asha Sharma',
      date_of_birth: null,
      email: null,
      kind: 'adult',
    });
  });

  it('offers only the API-accepted roles, defaulting to dependent', async () => {
    const select = await openAddForm();
    const options = Array.from(select.options);
    const values = options.map((option) => option.value);

    // The privileged roles the API now rejects with 422 are not offered.
    expect(values).not.toContain('primary_client');
    expect(values).not.toContain('collaborator');
    expect(screen.queryByRole('option', { name: /Primary client/ })).toBeNull();
    expect(screen.queryByRole('option', { name: /Collaborator/ })).toBeNull();

    // Every remaining option is intact, in order, and accepted by the API.
    expect(values).toEqual(['dependent', 'trusted_contact']);
    expect(options.map((option) => option.textContent)).toEqual([
      'Dependent (child or other dependent)',
      'Trusted contact',
    ]);

    // The default selection is the first option — an accepted role.
    expect(select.value).toBe('dependent');
  });

  it('resets the role to dependent after a successful add', async () => {
    const select = await openAddForm();
    fireEvent.change(select, { target: { value: 'trusted_contact' } });
    fireEvent.change(screen.getByLabelText('Full name'), {
      target: { value: 'Asha Sharma' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add person' }));

    await waitFor(() => {
      expect(createPerson).toHaveBeenCalledWith(
        'matter-1',
        expect.objectContaining({ relationship_role: 'trusted_contact' }),
      );
    });
    expect(await screen.findByText(/Person added/)).toBeDefined();

    // Reopening the form shows an accepted default, not a blank/stale role.
    fireEvent.click(screen.getByRole('button', { name: 'Add a person' }));
    const reopened = screen.getByLabelText(
      'Relationship to household',
    ) as HTMLSelectElement;
    expect(reopened.value).toBe('dependent');
  });

  it('surfaces a 422 from person creation as a visible form error', async () => {
    createPerson.mockRejectedValue(
      new Error(
        'relationship_role for a new person must be one of: dependent, trusted_contact.',
      ),
    );
    await openAddForm();
    fireEvent.change(screen.getByLabelText('Full name'), {
      target: { value: 'Asha Sharma' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add person' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('relationship_role for a new person');
    // The form stays open so the user can correct and retry.
    expect(screen.getByLabelText('Relationship to household')).toBeDefined();
  });
});
