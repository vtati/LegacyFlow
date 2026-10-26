/** @vitest-environment jsdom */
/**
 * Intake wizard section nav — per-section progress (Phase 3 QA defect 3).
 *
 * The API reports progress per question SET, and the nav used to render
 * that set percentage under every section of the set: answering one
 * question in "About your household" made "Quick check" show the same
 * 8.33%. The nav must show each section's own progress from the
 * payload's per-section breakdown.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

const setProgress = {
  total_questions: 3,
  answered: 1,
  required_total: 3,
  required_answered: 1,
  percent_complete: 33.33,
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
        title: 'Sharma plan',
        status: 'intake_in_progress',
        jurisdiction_code: 'US-VA',
        completeness_pct: 9,
        household: { id: 'hh-1', name: 'Sharma household' },
      },
    })),
    getQuestionSets: vi.fn(async () => ({
      question_sets: {
        household: {
          question_set: { id: 'household', title: 'Household profile' },
          sections: [
            {
              id: 'basics',
              title: 'About your household',
              questions: [
                { key: 'full_name', type: 'text', label: 'Full legal name?', required: true },
                { key: 'marital_status', type: 'text', label: 'Marital status?', required: true },
              ],
            },
            {
              id: 'triage',
              title: 'Quick check',
              questions: [
                { key: 'special_needs', type: 'text', label: 'Special needs?', required: true },
              ],
            },
          ],
        },
      },
    })),
    getAnswers: vi.fn(async () => ({ answers: { full_name: 'Raj Sharma' } })),
    getHousehold: vi.fn(async () => ({ persons: [] })),
    getIntakeProgress: vi.fn(async () => ({
      sets: { household: setProgress },
      sections: {
        household: {
          basics: { ...setProgress, total_questions: 2, answered: 1, percent_complete: 50 },
          triage: { ...setProgress, total_questions: 1, answered: 0, percent_complete: 0 },
        },
      },
      overall: setProgress,
      completeness_pct: 33.33,
    })),
    putAnswers: vi.fn(async () => ({ saved: true, progress: setProgress })),
  },
}));

import { IntakeWizard } from '../components/family/intake/intake-wizard';

describe('IntakeWizard section nav progress', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows each section its own percentage, not the set percentage twice', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nav = await screen.findByRole('navigation', { name: 'Intake sections' });
    expect(within(nav).getByText('50% complete')).toBeDefined();
    expect(within(nav).getByText('0% complete')).toBeDefined();
    expect(within(nav).queryByText('33.33% complete')).toBeNull();
  });
});
