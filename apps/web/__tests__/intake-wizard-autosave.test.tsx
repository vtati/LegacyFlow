/** @vitest-environment jsdom */
/**
 * Intake wizard — autosave serialization (2026-10-31).
 *
 * Every save PUTs the FULL answers map, so two saves in flight at
 * once can land at the server out of order: the slower, older map
 * arriving last silently reverts the newer answers while the UI
 * reports "saved". flushSave therefore chains sends — at most one
 * PUT in flight, a flush during a send queues behind it with the
 * newest snapshot. This test holds the first PUT open, edits again,
 * lets the debounce fire, and proves no second PUT starts until the
 * first settles — and that the queued PUT carries both edits.
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const control = vi.hoisted(() => ({
  putPayloads: [] as Record<string, unknown>[],
  releaseFirst: null as null | (() => void),
}));

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
        completeness_pct: 0,
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
          ],
        },
      },
    })),
    getAnswers: vi.fn(async () => ({ answers: {} })),
    getHousehold: vi.fn(async () => ({ persons: [] })),
    getIntakeProgress: vi.fn(async () => ({
      sets: {},
      sections: {},
      overall: {
        total_questions: 2,
        answered: 0,
        required_total: 2,
        required_answered: 0,
        percent_complete: 0,
      },
      completeness_pct: 0,
      documents: [],
    })),
    putAnswers: vi.fn((_matterId: string, answers: Record<string, unknown>) => {
      control.putPayloads.push(answers);
      if (control.putPayloads.length === 1) {
        return new Promise((resolve) => {
          control.releaseFirst = () => resolve({ answers, updated_at: null });
        });
      }
      return Promise.resolve({ answers, updated_at: null });
    }),
    evaluateIntake: vi.fn(async () => null),
    submitMatter: vi.fn(async () => ({})),
    createPerson: vi.fn(async () => ({})),
  },
}));

import { IntakeWizard } from '../components/family/intake/intake-wizard';

describe('IntakeWizard autosave', () => {
  it('never runs two saves concurrently; the queued save carries the newest map', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'Raj Sharma' } });

    // Debounce (800ms) fires the first save; it stays in flight.
    await waitFor(() => expect(control.putPayloads).toHaveLength(1), { timeout: 3000 });

    // A second edit's debounce fires while save #1 is still open.
    const maritalField = await screen.findByLabelText(/Marital status/);
    fireEvent.change(maritalField, { target: { value: 'married' } });
    await new Promise((resolve) => setTimeout(resolve, 1100));
    expect(control.putPayloads).toHaveLength(1);

    // Settling save #1 releases the queued save with BOTH edits.
    control.releaseFirst?.();
    await waitFor(() => expect(control.putPayloads).toHaveLength(2), { timeout: 3000 });
    expect(control.putPayloads[1]).toMatchObject({
      full_name: 'Raj Sharma',
      marital_status: 'married',
    });
  });
});
