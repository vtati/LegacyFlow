/** @vitest-environment jsdom */
/**
 * Intake wizard mobile responsiveness (2026-10-29, reported from iPhone):
 *
 * 1. The section nav rendered as a single-row horizontal scroller
 *    (`flex overflow-x-auto` with `shrink-0` items). As a grid item its
 *    automatic minimum size is the row's full min-content (~14 sections
 *    wide), which inflated the single mobile grid column to ~3,900px —
 *    the question card inherited that width and question labels ran off
 *    the right edge of the phone instead of wrapping.
 *    Contract: on small screens the nav WRAPS (grid), never scrolls
 *    sideways; the two-pane grid tracks are minmax(0, …) and both panes
 *    are min-w-0 so no child min-content can widen the page again.
 *
 * 2. The header meta line showed the API's generic "Household"
 *    placeholder: the matter summary embedded in the detail response
 *    carries the placeholder name, while the SAME response's top-level
 *    household object carries the real display name. The wizard must
 *    prefer the top-level household.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

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
        title: 'Tati Estate Plan',
        status: 'intake_in_progress',
        jurisdiction_code: 'US-VA',
        completeness_pct: 9,
        // The summary serializer's placeholder when the joined
        // household_name is absent on the detail path.
        household: { id: 'hh-1', name: 'Household' },
      },
      // The detail response's top-level household carries the real name.
      household: { id: 'hh-1', name: 'Tati Family' },
      progress: { completeness_pct: 9, status: 'intake_in_progress' },
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
              ],
            },
            {
              id: 'triage',
              title: 'Quick check',
              questions: [
                {
                  key: 'special_needs',
                  type: 'boolean',
                  label:
                    'Does any child have special needs requiring ongoing support?',
                  required: true,
                },
              ],
            },
          ],
        },
      },
    })),
    getAnswers: vi.fn(async () => ({ answers: {} })),
    getHousehold: vi.fn(async () => ({ persons: [] })),
    getIntakeProgress: vi.fn(async () => ({
      sets: { household: setProgress },
      sections: {
        household: {
          basics: { ...setProgress, percent_complete: 50 },
          triage: { ...setProgress, percent_complete: 0 },
        },
      },
      overall: setProgress,
      completeness_pct: 33.33,
    })),
    putAnswers: vi.fn(async () => ({ saved: true, progress: setProgress })),
    evaluateIntake: vi.fn(async () => ({ triggered: [] })),
    // The submit endpoint returns the FLAT MatterOut (no detail
    // envelope): embedded household is the placeholder and
    // jurisdiction_code is null, exactly as the backend serializes it.
    submitMatter: vi.fn(async () => ({
      id: 'matter-1',
      title: 'Tati Estate Plan',
      status: 'intake_submitted',
      jurisdiction_code: null,
      completeness_pct: 100,
      household: { id: 'hh-1', name: 'Household' },
      created_at: '2026-10-29T00:00:00Z',
      updated_at: '2026-10-29T00:00:00Z',
      assigned_attorney_id: null,
    })),
  },
}));

import { IntakeWizard } from '../components/family/intake/intake-wizard';

describe('IntakeWizard mobile layout contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('wraps the section nav instead of scrolling it horizontally', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nav = await screen.findByRole('navigation', { name: 'Intake sections' });
    const list = nav.querySelector('ol');
    expect(list).not.toBeNull();
    const listClass = list?.getAttribute('class') ?? '';
    // No horizontal scrolling at any breakpoint below lg.
    expect(listClass).not.toContain('overflow-x-auto');
    expect(listClass).not.toContain('overflow-x-scroll');
    // It wraps into a grid on small screens and becomes the desktop
    // sidebar column at lg.
    expect(listClass).toContain('grid');
    expect(listClass).toContain('grid-cols-2');
    expect(listClass).toContain('sm:grid-cols-3');
    expect(listClass).toContain('lg:flex');
    expect(listClass).toContain('lg:flex-col');
    // Items must be allowed to shrink/wrap — shrink-0 is what gave the
    // row its page-widening min-content.
    for (const item of list?.querySelectorAll('li') ?? []) {
      const itemClass = item.getAttribute('class') ?? '';
      expect(itemClass).not.toContain('shrink-0');
      expect(itemClass).toContain('min-w-0');
    }
  });

  it('clamps the two-pane grid so child min-content cannot widen the page', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nav = await screen.findByRole('navigation', { name: 'Intake sections' });
    const grid = nav.parentElement;
    expect(grid).not.toBeNull();
    const gridClass = grid?.getAttribute('class') ?? '';
    expect(gridClass).toContain('grid-cols-[minmax(0,1fr)]');
    expect(gridClass).toContain('lg:grid-cols-[260px_minmax(0,1fr)]');
    expect(nav.getAttribute('class') ?? '').toContain('min-w-0');
    const contentPane = nav.nextElementSibling;
    expect(contentPane).not.toBeNull();
    expect(contentPane?.getAttribute('class') ?? '').toContain('min-w-0');
  });

  it('shows the real household name from the detail response, not the placeholder', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const meta = await screen.findByText(/Tati Family/);
    expect(meta).toBeDefined();
    expect(screen.queryByText(/^Household ·/)).toBeNull();
    expect(meta.textContent).toContain('US-VA');
  });

  it('survives submit: flat response shape keeps the real household name and renders the new status', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    await screen.findByText(/Tati Family/);
    fireEvent.click(screen.getByRole('button', { name: 'Submit for attorney review' }));
    // The submitted card rendering at all proves the post-submit state
    // is a complete matter (a partial object crashes formatStatus).
    expect(await screen.findByText('Submitted for attorney review')).toBeDefined();
    const meta = await screen.findByText(/Tati Family/);
    expect(meta.textContent).toContain('US-VA');
  });
});
