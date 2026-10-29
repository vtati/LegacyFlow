/** @vitest-environment jsdom */
/**
 * Intake wizard mobile section navigation (2026-10-29, Venugopal UX
 * direction after two iterations from iPhone):
 *
 * History: the nav began as a single-row horizontal scroller whose
 * min-content (~21 sections) inflated the single mobile grid column
 * to ~3,900px and pushed the question card off-screen. The first fix
 * wrapped the nav into a 2-column grid — no overflow, but switching
 * sections meant scrolling a long index, pushing the questions far
 * down the page.
 *
 * Current contract (below lg): the nav is a STICKY HORIZONTAL TAB
 * STRIP — the standard pattern for long multi-section forms. Chips
 * scroll horizontally INSIDE the strip only; the page itself must
 * never scroll sideways. Overflow safety by construction:
 *   - the two-pane wrapper is a flex column below lg (a sticky strip
 *     cannot travel inside a grid row the height of itself) and the
 *     lg grid keeps its minmax(0, …) tracks;
 *   - both panes are min-w-0, and the strip is a scroll container, so
 *     chip min-content can never widen the page again.
 * At lg the same <ol> becomes the vertical sidebar, unchanged.
 *
 * Chips carry glanceable completion: a check at 100%, otherwise a
 * compact percent (the full "% complete" line + progress bar remain
 * the desktop sidebar rendering). Switching section — by chip, by
 * Previous/Next, or programmatically — centers the active chip in
 * the strip by adjusting the strip's own scrollLeft, and scrolls the
 * question card to just below the sticky strip.
 *
 * jsdom has no layout engine, so the assertions below pin the
 * structural tokens and the scroll arithmetic (with geometry stubbed
 * in). The geometric half of the contract — document scrollWidth vs
 * viewport, sticky travel, chip visibility after a tap — is measured
 * against production with Playwright on every release of this file's
 * component (see hidden_files/intake-tab-strip-2026-10-29/).
 *
 * Also pinned here: the header meta line shows the REAL household
 * name from the matter-detail response's top-level household object
 * (the embedded summary carries the generic "Household" placeholder),
 * and that name survives submit (the submit endpoint returns a flat
 * MatterOut whose embedded household is the placeholder and whose
 * jurisdiction_code is null).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

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
                  label: 'Does any child have special needs requiring ongoing support?',
                  required: true,
                },
              ],
            },
            {
              id: 'wishes',
              title: 'Final wishes',
              questions: [
                { key: 'burial', type: 'text', label: 'Burial or cremation?', required: true },
              ],
            },
            {
              id: 'almost',
              title: 'Almost there',
              questions: [
                { key: 'notes', type: 'text', label: 'Anything else?', required: false },
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
          triage: { ...setProgress, percent_complete: 100 },
          wishes: { ...setProgress, percent_complete: 0 },
          almost: { ...setProgress, percent_complete: 99.6 },
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
import { api } from '@/lib/api-client';

describe('IntakeWizard mobile tab strip contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a sticky horizontal tab strip below lg that scrolls within itself', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nav = await screen.findByRole('navigation', { name: 'Intake sections' });
    // The strip pins under the app-shell header (h-16) on small
    // screens; at lg it is the sidebar, stuck at top-24 as before.
    const navClass = nav.getAttribute('class') ?? '';
    expect(navClass).toContain('sticky');
    expect(navClass).toContain('top-16');
    expect(navClass).toContain('lg:top-24');
    expect(navClass).toContain('min-w-0');
    // Full-bleed to the viewport edges: -mx-4 exactly cancels the app
    // shell's px-4 content inset (16px) — no more, or the page itself
    // would overflow; the sidebar resets it at lg.
    expect(navClass).toContain('-mx-4');
    expect(navClass).toContain('px-4');
    expect(navClass).toContain('lg:mx-0');

    const list = nav.querySelector('ol');
    expect(list).not.toBeNull();
    const listClass = list?.getAttribute('class') ?? '';
    // One nowrap row that scrolls internally — never a wrapping grid.
    expect(listClass).toContain('flex');
    expect(listClass).toContain('flex-nowrap');
    expect(listClass).toContain('overflow-x-auto');
    expect(listClass).not.toContain('grid-cols-2');
    expect(listClass).not.toContain('sm:grid-cols-3');
    // Momentum-friendly internal scrolling: no visible scrollbar,
    // scroll chaining contained so the page doesn't take over.
    expect(listClass).toContain('[scrollbar-width:none]');
    expect(listClass).toContain('overscroll-x-contain');
    // At lg the same list is the vertical sidebar column.
    expect(listClass).toContain('lg:flex-col');
    expect(listClass).toContain('lg:overflow-x-visible');
    // Chips size to their content in the strip (shrink-0) — safe now
    // that the strip is a min-w-0 scroll container, not a grid track.
    for (const item of list?.querySelectorAll('li') ?? []) {
      expect(item.getAttribute('class') ?? '').toContain('shrink-0');
    }
  });

  it('clamps the layout so chip min-content cannot widen the page', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nav = await screen.findByRole('navigation', { name: 'Intake sections' });
    const wrapper = nav.parentElement;
    expect(wrapper).not.toBeNull();
    const wrapperClass = wrapper?.getAttribute('class') ?? '';
    // Flex column on mobile (so the sticky strip can travel the full
    // content height); the lg grid keeps minmax(0, …) tracks.
    expect(wrapperClass).toContain('flex');
    expect(wrapperClass).toContain('flex-col');
    expect(wrapperClass).toContain('lg:grid');
    expect(wrapperClass).toContain('lg:grid-cols-[260px_minmax(0,1fr)]');
    expect(nav.getAttribute('class') ?? '').toContain('min-w-0');
    const contentPane = nav.nextElementSibling;
    expect(contentPane).not.toBeNull();
    expect(contentPane?.getAttribute('class') ?? '').toContain('min-w-0');
  });

  it('gives chips a 44px touch target and glanceable completion state', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nav = await screen.findByRole('navigation', { name: 'Intake sections' });

    const basicsChip = within(nav).getByRole('button', { name: /About your household/ });
    const basicsClass = basicsChip.getAttribute('class') ?? '';
    expect(basicsClass).toContain('min-h-11');
    expect(basicsClass).toContain('rounded-full');
    expect(basicsClass).toContain('lg:rounded-lf-control');
    // Partial section: compact percent on the chip; the desktop
    // "% complete" line is still in the DOM for the sidebar. The two
    // renderings are breakpoint-gated so exactly one is visible.
    const compact = within(basicsChip).getByText('50%');
    expect(compact.getAttribute('class') ?? '').toContain('lg:hidden');
    const desktopLine = within(basicsChip).getByText('50% complete');
    expect(desktopLine.getAttribute('class') ?? '').toContain('hidden');
    expect(desktopLine.getAttribute('class') ?? '').toContain('lg:block');
    // Mobile screen readers still hear the word "complete".
    expect(within(basicsChip).getByText('complete')).toBeDefined();

    // 100% section: a check icon instead of a percent figure.
    const triageChip = within(nav).getByRole('button', { name: /Quick check/ });
    expect(triageChip.querySelector('svg.lucide-check')).not.toBeNull();
    expect(within(triageChip).queryByText('100%')).toBeNull();
    expect(within(triageChip).getByText('complete')).toBeDefined();

    // Untouched section: honest 0%.
    const wishesChip = within(nav).getByRole('button', { name: /Final wishes/ });
    expect(within(wishesChip).getByText('0%')).toBeDefined();

    // 99.6% is NOT complete: the chip shows a floored 99%, never a
    // rounded-up "100%", and no check icon.
    const almostChip = within(nav).getByRole('button', { name: /Almost there/ });
    expect(within(almostChip).getByText('99%')).toBeDefined();
    expect(within(almostChip).queryByText('100%')).toBeNull();
    expect(almostChip.querySelector('svg.lucide-check')).toBeNull();

    // Exactly one chip is the current step.
    const current = within(nav)
      .getAllByRole('button')
      .filter((b) => b.getAttribute('aria-current') === 'step');
    expect(current).toHaveLength(1);
    expect(current[0]).toBe(basicsChip);
  });

  it('centers the active chip in the strip and scrolls the card into view on section change', async () => {
    const originalScrollIntoView = window.HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      render(<IntakeWizard matterId="matter-1" />);
      const nav = await screen.findByRole('navigation', { name: 'Intake sections' });
      const strip = nav.querySelector('ol') as HTMLOListElement;
      const stripScrollTo = vi.fn();
      (strip as unknown as { scrollTo: typeof stripScrollTo }).scrollTo = stripScrollTo;
      // Geometry jsdom cannot compute: a 340px-wide strip holding
      // 1400px of chips. Centering target for a chip =
      // offsetLeft - (clientWidth - offsetWidth) / 2, clamped to
      // [0, scrollWidth - clientWidth] = [0, 1060].
      Object.defineProperty(strip, 'clientWidth', { value: 340, configurable: true });
      Object.defineProperty(strip, 'scrollWidth', { value: 1400, configurable: true });
      const chips = within(nav).getAllByRole('button');
      const setGeom = (el: HTMLElement, left: number, width: number) => {
        Object.defineProperty(el, 'offsetLeft', { value: left, configurable: true });
        Object.defineProperty(el, 'offsetWidth', { value: width, configurable: true });
      };

      // Via the wizard's Next button → section 2 at offsetLeft 200:
      // target = 200 - (340 - 100) / 2 = 80. Focus moves to the new
      // section's heading.
      setGeom(chips[1], 200, 100);
      fireEvent.click(screen.getByRole('button', { name: 'Next' }));
      const heading = await screen.findByRole('heading', { name: 'Quick check' });
      expect(document.activeElement).toBe(heading);
      expect(scrollIntoView).toHaveBeenCalled();
      expect(stripScrollTo).toHaveBeenCalledWith(expect.objectContaining({ left: 80 }));
      const current = within(nav)
        .getAllByRole('button')
        .filter((b) => b.getAttribute('aria-current') === 'step');
      expect(current).toHaveLength(1);
      expect(current[0].textContent).toContain('Quick check');

      // Via a chip tap → 'Final wishes' at offsetLeft 1000:
      // target = 1000 - (340 - 140) / 2 = 900.
      setGeom(chips[2], 1000, 140);
      stripScrollTo.mockClear();
      scrollIntoView.mockClear();
      fireEvent.click(within(nav).getByRole('button', { name: /Final wishes/ }));
      expect(await screen.findByRole('heading', { name: 'Final wishes' })).toBeDefined();
      expect(scrollIntoView).toHaveBeenCalled();
      expect(stripScrollTo).toHaveBeenCalledWith(expect.objectContaining({ left: 900 }));

      // Back to section 1: raw target = 0 - (340 - 120) / 2 = -110,
      // clamped to 0.
      setGeom(chips[0], 0, 120);
      stripScrollTo.mockClear();
      fireEvent.click(within(nav).getByRole('button', { name: /About your household/ }));
      expect(await screen.findByRole('heading', { name: 'About your household' })).toBeDefined();
      expect(stripScrollTo).toHaveBeenCalledWith(expect.objectContaining({ left: 0 }));
    } finally {
      window.HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it(
    'does not re-center the strip on autosaves that change no percentage, but does when one changes',
    async () => {
      render(<IntakeWizard matterId="matter-1" />);
      const nav = await screen.findByRole('navigation', { name: 'Intake sections' });
      const strip = nav.querySelector('ol') as HTMLOListElement;
      const stripScrollTo = vi.fn();
      (strip as unknown as { scrollTo: typeof stripScrollTo }).scrollTo = stripScrollTo;
      Object.defineProperty(strip, 'clientWidth', { value: 340, configurable: true });
      Object.defineProperty(strip, 'scrollWidth', { value: 1400, configurable: true });
      const getProgress = api.getIntakeProgress as unknown as ReturnType<typeof vi.fn>;
      const baselineCalls = getProgress.mock.calls.length;

      // Type an answer → autosave → progress refetched as a NEW
      // object with the SAME percentages (the factory mock builds a
      // fresh literal per call). The strip must not be yanked back.
      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'A' } });
      await vi.waitFor(
        () => expect(getProgress.mock.calls.length).toBe(baselineCalls + 1),
        { timeout: 4000 },
      );
      await new Promise((resolve) => setTimeout(resolve, 150));
      expect(stripScrollTo).not.toHaveBeenCalled();

      // Now a save whose progress changes a percentage (Quick check
      // 100 → 50): the chip's label changes width, so re-centering
      // is correct and must happen.
      getProgress.mockResolvedValue({
        sets: { household: setProgress },
        sections: {
          household: {
            basics: { ...setProgress, percent_complete: 50 },
            triage: { ...setProgress, percent_complete: 50 },
            wishes: { ...setProgress, percent_complete: 0 },
            almost: { ...setProgress, percent_complete: 99.6 },
          },
        },
        overall: setProgress,
        completeness_pct: 33.33,
      });
      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'AB' } });
      await vi.waitFor(() => expect(stripScrollTo).toHaveBeenCalled(), { timeout: 4000 });
    },
    15000,
  );

  it('fades only the strip edges that have more chips beyond them', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nav = await screen.findByRole('navigation', { name: 'Intake sections' });
    const strip = nav.querySelector('ol') as HTMLOListElement;
    Object.defineProperty(strip, 'clientWidth', { value: 340, configurable: true });
    Object.defineProperty(strip, 'scrollWidth', { value: 1400, configurable: true });
    Object.defineProperty(strip, 'scrollLeft', {
      value: 0,
      configurable: true,
      writable: true,
    });
    // At the left edge with more chips to the right: trailing fade
    // only — the first chip renders crisp.
    fireEvent.scroll(strip);
    expect(strip.style.maskImage).toContain('calc(100% - 20px)');
    expect(strip.style.maskImage).not.toContain('transparent 0');
    // Scrolled into the middle: both edges fade.
    Object.defineProperty(strip, 'scrollLeft', {
      value: 500,
      configurable: true,
      writable: true,
    });
    fireEvent.scroll(strip);
    expect(strip.style.maskImage).toContain('transparent 0');
    expect(strip.style.maskImage).toContain('calc(100% - 20px)');
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
