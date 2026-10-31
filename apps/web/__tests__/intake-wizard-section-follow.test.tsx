/** @vitest-environment jsdom */
/**
 * Intake wizard — section following across applicability changes (L9).
 *
 * When the visible section list changes under the family (a gate
 * answer prunes a section), the wizard must stay on the section the
 * family was actually reading — or land on its nearest survivor —
 * never teleport to the end of the list or point at a different
 * section under the same index. This pack's "Extras" section gates
 * BOTH of its questions on its own gate answer, so answering "No"
 * while standing on it prunes the section out from under the reader:
 * the wizard should land on the next surviving section ("Final").
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const setProgress = {
  total_questions: 3,
  answered: 0,
  required_total: 3,
  required_answered: 0,
  percent_complete: 0,
};

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    listMatters: vi.fn(async () => ({ items: [] })),
    getMatter: vi.fn(async () => ({
      matter: {
        id: 'matter-1',
        title: 'Plan',
        status: 'intake_in_progress',
        jurisdiction_code: 'US-VA',
        completeness_pct: 0,
        household: { id: 'hh-1', name: 'Household' },
      },
    })),
    getQuestionSets: vi.fn(async () => ({
      question_sets: {
        flow: {
          question_set: { id: 'flow', title: 'Flow' },
          sections: [
            {
              id: 'start',
              title: 'Start here',
              questions: [
                { key: 'full_name', type: 'text', label: 'Full legal name?', required: true },
              ],
            },
            {
              id: 'extras',
              title: 'Extras',
              questions: [
                {
                  key: 'wants_extras',
                  type: 'boolean',
                  label: 'Do you want extras?',
                  required: false,
                  applies_if: 'wants_extras == true',
                },
                {
                  key: 'extras_detail',
                  type: 'text',
                  label: 'Describe the extras',
                  required: true,
                  applies_if: 'wants_extras == true',
                },
              ],
            },
            {
              id: 'final',
              title: 'Final',
              questions: [
                { key: 'closing_notes', type: 'text', label: 'Anything else?', required: false },
              ],
            },
            {
              id: 'alpha',
              title: 'Alpha',
              questions: [
                {
                  key: 'alpha_detail',
                  type: 'text',
                  label: 'Alpha detail',
                  required: true,
                  applies_if: 'include_details == true',
                },
              ],
            },
            {
              id: 'beta',
              title: 'Beta',
              questions: [
                {
                  key: 'beta_detail',
                  type: 'text',
                  label: 'Beta detail',
                  required: true,
                  applies_if: 'include_details == true',
                },
              ],
            },
            {
              id: 'gamma',
              title: 'Gamma',
              questions: [
                { key: 'include_details', type: 'boolean', label: 'Include the detail sections?', required: false },
                { key: 'gamma_note', type: 'text', label: 'Gamma note', required: false },
              ],
            },
            {
              id: 'delta',
              title: 'Delta',
              questions: [
                {
                  key: 'delta_detail',
                  type: 'text',
                  label: 'Delta detail',
                  required: true,
                  applies_if: 'include_details == true',
                },
              ],
            },
            {
              id: 'echo',
              title: 'Echo',
              questions: [
                { key: 'echo_note', type: 'text', label: 'Echo note', required: false },
              ],
            },
          ],
        },
      },
    })),
    getAnswers: vi.fn(async () => ({ answers: {} })),
    getHousehold: vi.fn(async () => ({ persons: [] })),
    getIntakeProgress: vi.fn(async () => ({
      sets: { flow: setProgress },
      sections: { flow: {} },
      overall: setProgress,
      completeness_pct: 0,
      documents: [],
    })),
    putAnswers: vi.fn(async () => ({ answers: {}, updated_at: null })),
    evaluateIntake: vi.fn(async () => null),
    submitMatter: vi.fn(async () => ({})),
    createPerson: vi.fn(async () => ({})),
  },
}));

import { IntakeWizard } from '../components/family/intake/intake-wizard';

describe('IntakeWizard section following', () => {
  it('lands on the nearest surviving section when the active section prunes itself', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    await screen.findByLabelText(/Full legal name/);
    const extrasNav = screen
      .getAllByRole('button')
      .filter((b) => b.textContent?.includes('Extras'));
    fireEvent.click(extrasNav[0]);
    expect(await screen.findByRole('heading', { name: 'Extras' })).toBeDefined();

    // Answer the section's own gate "No": both questions prune, the
    // section leaves the nav, and the reader lands on "Final" — the
    // nearest survivor — not on "Start here" and not nowhere.
    const noRadio = document.querySelector('input[type="radio"][value="false"]');
    expect(noRadio).not.toBeNull();
    fireEvent.click(noRadio as HTMLElement);
    expect(await screen.findByRole('heading', { name: 'Final' })).toBeDefined();
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Extras' })).toBeNull(),
    );
  });

  it('stays on the active section when several EARLIER sections prune at once (F2)', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    await screen.findByLabelText(/Full legal name/);
    const gammaNav = screen
      .getAllByRole('button')
      .filter((b) => b.textContent?.includes('Gamma'));
    fireEvent.click(gammaNav[0]);
    expect(await screen.findByRole('heading', { name: 'Gamma' })).toBeDefined();

    // One answer prunes Alpha, Beta and Delta simultaneously. Gamma
    // survives and must REMAIN the active section — a clamp-first
    // implementation teleports the reader to Echo (the last section)
    // because Gamma's raw index falls out of range.
    const noRadio = document.querySelector('input[type="radio"][value="false"]');
    expect(noRadio).not.toBeNull();
    fireEvent.click(noRadio as HTMLElement);
    await waitFor(() => {
      const navText = screen
        .getAllByRole('button')
        .map((b) => b.textContent ?? '')
        .join(' | ');
      expect(navText).not.toContain('Alpha');
      expect(navText).not.toContain('Delta');
    });
    expect(screen.getByRole('heading', { name: 'Gamma' })).toBeDefined();
    expect(screen.queryByRole('heading', { name: 'Echo' })).toBeNull();
  });
});
