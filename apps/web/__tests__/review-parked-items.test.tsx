/** @vitest-environment jsdom */
/**
 * Review-screen parked open-items section (Phase 4 integration): items
 * the family parked with "don't know yet" during the interview surface
 * on /family/review with their labels and a path back into the
 * interview to resolve each one. The section renders only when the
 * parked-items endpoint returns open items.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ParkedItem } from '../lib/api-client';
import { ReviewParkedItems } from '../components/family/review/review-parked-items';

const PARKED: ParkedItem[] = [
  {
    question_key: 'primary_guardian_person',
    label: 'Primary guardian for your children',
    required: true,
    parked_at: '2026-10-26T12:00:00Z',
  },
  {
    question_key: 'poa_agent_person',
    label: 'Financial agent (power of attorney)',
    required: true,
    parked_at: '2026-10-26T12:05:00Z',
  },
];

describe('ReviewParkedItems', () => {
  it('lists each parked item with a link back to the interview', () => {
    render(<ReviewParkedItems matterId="matter-1" items={PARKED} />);

    expect(screen.getByText('Saved for later — still to finish')).toBeDefined();
    expect(screen.getByText('Primary guardian for your children')).toBeDefined();
    expect(screen.getByText('Financial agent (power of attorney)')).toBeDefined();
    const links = screen.getAllByRole('link', { name: 'Answer in your interview' });
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link.getAttribute('href')).toBe('/family/interview/matter-1');
    }
    expect(screen.getByText(/be created until each one is answered/)).toBeDefined();
  });

  it('renders nothing when no items are parked', () => {
    const { container } = render(<ReviewParkedItems matterId="matter-1" items={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/* Page-level wiring: the review page fetches + renders the section.  */
/* ------------------------------------------------------------------ */

const matter = {
  id: 'matter-1',
  title: 'Sharma plan',
  status: 'INTAKE_IN_PROGRESS',
  jurisdiction_code: 'US-VA',
  completeness_pct: 100,
  household: { id: 'hh-1', name: 'Sharma household' },
};

const hoisted = vi.hoisted(() => ({
  listParkedItems: vi.fn(),
}));
hoisted.listParkedItems.mockImplementation(async () => PARKED);

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
          sections: [],
        },
      },
    })),
    getAnswers: vi.fn(async () => ({
      answers: {},
      updated_at: '2026-10-26T00:00:00Z',
    })),
    getIntakeProgress: vi.fn(async () => ({
      sets: {},
      overall: {
        total_questions: 48,
        answered: 46,
        required_total: 41,
        required_answered: 39,
        percent_complete: 95,
      },
      completeness_pct: 95,
    })),
    getHousehold: vi.fn(async () => ({
      household: { id: 'hh-1', name: 'Sharma household' },
      members: [],
      persons: [],
      relationships: null,
    })),
    listNominations: vi.fn(async () => []),
    getDraftSetState: vi.fn(async () => ({
      enabled: true,
      matter_status: 'INTAKE_IN_PROGRESS',
      applicable: { required_total: 41, required_answered: 39, complete: false, missing_labels: [] },
      documents: [],
      can_generate: false,
      can_submit: false,
      submit_blockers: ['not_complete'],
      assigned_attorney: null,
      latest_packet: null,
    })),
    putAnswers: vi.fn(async () => ({ saved: true, progress: {} })),
    updatePerson: vi.fn(async () => ({})),
    generateDraftSet: vi.fn(async () => ({})),
    submitDraftSet: vi.fn(async () => ({})),
    listParkedItems: hoisted.listParkedItems,
  },
}));

import FamilyReviewPage from '../app/(app)/family/review/page';

describe('FamilyReviewPage parked section', () => {
  it('shows parked items returned by the endpoint', async () => {
    hoisted.listParkedItems.mockImplementation(async () => PARKED);
    render(<FamilyReviewPage />);

    expect(await screen.findByText('Saved for later — still to finish')).toBeDefined();
    expect(screen.getByText('Primary guardian for your children')).toBeDefined();
    expect(hoisted.listParkedItems).toHaveBeenCalledWith('matter-1');
  });

  it('hides the section when the endpoint returns none', async () => {
    hoisted.listParkedItems.mockImplementation(async () => []);
    render(<FamilyReviewPage />);

    expect(await screen.findByText('Sharma plan')).toBeDefined();
    expect(screen.queryByText('Saved for later — still to finish')).toBeNull();
  });
});
