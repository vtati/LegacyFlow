/** @vitest-environment jsdom */
/**
 * Review page wiring (Part 2): the page must refetch the draft-set state
 * after an answer save — that refetch is what makes an edit visibly mark
 * drafts stale (D4/D5) without a reload.
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const matter = {
  id: 'matter-1',
  title: 'Sharma plan',
  status: 'INTAKE_IN_PROGRESS',
  jurisdiction_code: 'US-VA',
  completeness_pct: 100,
  household: { id: 'hh-1', name: 'Sharma household' },
};

const draftState = {
  enabled: true,
  matter_status: 'INTAKE_IN_PROGRESS',
  applicable: { required_total: 41, required_answered: 41, complete: true, missing_labels: [] },
  documents: [],
  can_generate: true,
  can_submit: false,
  submit_blockers: ['drafts_missing'],
  assigned_attorney: null,
  latest_packet: null,
};

const hoisted = vi.hoisted(() => ({
  getDraftSetState: vi.fn(),
  putAnswers: vi.fn(),
}));
const { getDraftSetState, putAnswers } = hoisted;
getDraftSetState.mockImplementation(async () => draftState);
putAnswers.mockImplementation(async () => ({ saved: true, progress: {} }));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    listMatters: vi.fn(async () => ({ items: [matter] })),
    getQuestionSets: vi.fn(async () => ({
      jurisdiction_code: 'US-VA',
      pack_version: '2.0.0',
      question_sets: {
        household: {
          question_set: { id: 'household', title: 'About your household' },
          sections: [
            {
              id: 'basics',
              title: 'Basics',
              questions: [
                { key: 'full_legal_name', type: 'text', label: 'Your full legal name', required: true },
              ],
            },
          ],
        },
        guardians: { question_set: { id: 'guardians', title: 'Guardians' }, sections: [] },
        assets: { question_set: { id: 'assets', title: 'Assets' }, sections: [] },
      },
    })),
    getAnswers: vi.fn(async () => ({
      answers: { full_legal_name: 'Priya Sharms' },
      updated_at: '2026-10-26T00:00:00Z',
    })),
    getIntakeProgress: vi.fn(async () => ({
      sets: {},
      overall: {
        total_questions: 48,
        answered: 48,
        required_total: 41,
        required_answered: 41,
        percent_complete: 100,
      },
      completeness_pct: 100,
    })),
    getHousehold: vi.fn(async () => ({
      household: { id: 'hh-1', name: 'Sharma household' },
      members: [],
      persons: [],
      relationships: null,
    })),
    listNominations: vi.fn(async () => []),
    listParkedItems: vi.fn(async () => []),
    getDraftSetState: hoisted.getDraftSetState,
    putAnswers: hoisted.putAnswers,
    updatePerson: vi.fn(async () => ({})),
    generateDraftSet: vi.fn(async () => ({})),
    submitDraftSet: vi.fn(async () => ({})),
  },
}));

import FamilyReviewPage from '../app/(app)/family/review/page';

describe('FamilyReviewPage wiring', () => {
  it('refetches the draft state after an answer save', async () => {
    render(<FamilyReviewPage />);
    expect(await screen.findByText('Priya Sharms')).toBeDefined();
    const callsBefore = getDraftSetState.mock.calls.length;
    expect(callsBefore).toBeGreaterThanOrEqual(1);

    fireEvent.click(screen.getByRole('button', { name: /Edit answer: Your full legal name/ }));
    const input = screen.getByDisplayValue('Priya Sharms') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Priya Sharma' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(putAnswers).toHaveBeenCalledWith('matter-1', { full_legal_name: 'Priya Sharma' });
    });
    await waitFor(() => {
      expect(getDraftSetState.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });
});
