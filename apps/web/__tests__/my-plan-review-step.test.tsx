/** @vitest-environment jsdom */
/**
 * My Plan review entry point (Part 2 of the self-serve draft plan).
 *
 * When a matter's intake is complete, its status is pre-submission, and
 * the draft-set state probe reports the firm has self-serve draft
 * generation enabled, the plan card's next steps lead with
 * "Review & create your drafts" -> /family/review. When the probe says
 * disabled, no review step appears.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const matter = {
  id: 'matter-1',
  title: 'Sharma plan',
  status: 'INTAKE_IN_PROGRESS',
  jurisdiction_code: 'US-VA',
  completeness_pct: 100,
  household: { id: 'hh-1', name: 'Sharma household' },
};

const hoisted = vi.hoisted(() => ({ draftEnabled: true }));

const emptySet = {
  question_set: { id: 's', title: 'Set' },
  sections: [],
};

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    listMatters: vi.fn(async () => ({ items: [matter] })),
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
    getAnswers: vi.fn(async () => ({ answers: {}, updated_at: '2026-10-26T00:00:00Z' })),
    getQuestionSets: vi.fn(async () => ({
      jurisdiction_code: 'US-VA',
      pack_version: '2.0.0',
      question_sets: { household: emptySet, guardians: emptySet, assets: emptySet },
    })),
    getHousehold: vi.fn(async () => ({
      household: { id: 'hh-1', name: 'Sharma household' },
      members: [],
      persons: [],
      relationships: null,
    })),
    listNominations: vi.fn(async () => []),
    getDraftSetState: vi.fn(async () => ({ enabled: hoisted.draftEnabled })),
  },
}));

import MyPlanPage from '../app/(app)/family/my-plan/page';

describe('My Plan review entry point', () => {
  it('leads with the review step when drafts are enabled', async () => {
    hoisted.draftEnabled = true;
    render(<MyPlanPage />);
    const link = await screen.findByRole('link', { name: 'Review & create your drafts' });
    expect(link.getAttribute('href')).toBe('/family/review');
  });

  it('omits the review step when drafts are disabled', async () => {
    hoisted.draftEnabled = false;
    render(<MyPlanPage />);
    // the card renders (a nomination step proves the load finished)
    expect(
      await screen.findByRole('link', { name: 'Name a guardian for your children' }),
    ).toBeDefined();
    expect(screen.queryByRole('link', { name: 'Review & create your drafts' })).toBeNull();
  });
});
