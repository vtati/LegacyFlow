/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { SectionNav } from '../components/section-nav';
import { FAMILY_NAV } from '../lib/nav';

describe('SectionNav (family)', () => {
  it('renders all family nav items (§5.5)', () => {
    render(<SectionNav section="family" />);
    const nav = screen.getByRole('navigation', { name: 'family navigation' });
    const links = within(nav).getAllByRole('link');
    expect(links).toHaveLength(FAMILY_NAV.length);
    // 12 is intentional: the nav dedup (daf83c8) removed duplicate
    // entries — this count was the stale side of that change (UI-019).
    expect(FAMILY_NAV).toHaveLength(12);
    for (const item of FAMILY_NAV) {
      expect(within(nav).getByRole('link', { name: item.label })).toBeDefined();
    }
  });

  it('marks the current page with aria-current', () => {
    // vitest.setup.ts mocks usePathname() => '/family/home'
    render(<SectionNav section="family" />);
    const home = screen.getByRole('link', { name: 'Home' });
    expect(home).toHaveAttribute('aria-current', 'page');
  });
});
