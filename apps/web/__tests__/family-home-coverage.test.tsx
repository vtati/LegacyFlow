/** @vitest-environment jsdom */
/**
 * Family home coverage consistency (Phase 3 QA defect 2).
 *
 * The stored `completeness_pct` on a matter is a lifecycle proxy (set
 * from the matter's status at creation and on every transition), while
 * the welcome band's ring reads the LIVE intake-progress figure. For a
 * fresh matter the same screen showed ring 0% and card 9%. Every
 * coverage number on family home must resolve from the live figure for
 * that matter, so a matter never shows two percentages at once.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const matter = {
  id: 'matter-1',
  title: 'Sharma plan',
  status: 'intake_in_progress',
  jurisdiction_code: 'US-VA',
  completeness_pct: 9, // stored lifecycle proxy — must not reach the screen
  household: { id: 'hh-1', name: 'Sharma household' },
};

const hoisted = vi.hoisted(() => ({
  completenessPct: 2,
  requiredAnswered: 1,
  draftEnabled: false,
}));

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
        answered: hoisted.requiredAnswered,
        required_total: 41,
        required_answered: hoisted.requiredAnswered,
        percent_complete: hoisted.completenessPct,
      },
      completeness_pct: hoisted.completenessPct,
    })),
    // Draft-set probe (review entry point): disabled unless a test opts in.
    getDraftSetState: vi.fn(async () => ({ enabled: hoisted.draftEnabled })),
  },
}));

import FamilyHomePage from '../app/(app)/family/home/page';

describe('FamilyHomePage coverage', () => {
  it('shows the live figure in the band, the ring, the stats, and the matter card', async () => {
    render(<FamilyHomePage />);

    // Welcome band line for the primary matter.
    expect(await screen.findByText(/2% of intake complete/)).toBeDefined();
    // The matter card's "Intake completeness" row.
    expect(await screen.findByText('Intake completeness')).toBeDefined();
    // Nothing on the screen may show the stored 9% proxy — not as text,
    // not in the ring's accessible value.
    expect(screen.queryByText('9%')).toBeNull();
    expect(screen.queryByText(/9% complete/)).toBeNull();
    // Every progressbar on the page (ring, average tile, matter card)
    // carries the same live value.
    const bars = screen.getAllByRole('progressbar');
    expect(bars.length).toBeGreaterThanOrEqual(3);
    for (const bar of bars) {
      expect(bar.getAttribute('aria-valuenow')).toBe('2');
    }
  });

  it('routes a complete intake to the review screen when draft generation is enabled', async () => {
    hoisted.completenessPct = 100;
    hoisted.requiredAnswered = 41;
    hoisted.draftEnabled = true;
    try {
      render(<FamilyHomePage />);
      const step = await screen.findByText('Review & create your drafts');
      expect(step).toBeDefined();
      const link = screen.getByRole('link', { name: /Review & create drafts/ });
      expect(link.getAttribute('href')).toBe('/family/review');
    } finally {
      hoisted.completenessPct = 2;
      hoisted.requiredAnswered = 1;
      hoisted.draftEnabled = false;
    }
  });
});
