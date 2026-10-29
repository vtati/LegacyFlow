/** @vitest-environment jsdom */
/**
 * UI-002/UI-003: heading order on the auth pages. The form column's card
 * title is the page's primary heading (h1, describing the page purpose);
 * the editorial slogan must not be an h1 that follows an h3.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import LoginPage from '../app/login/page';
import SignUpPage from '../app/signup/page';
import { CardTitle, CardTitleEditorial } from '../components/ui/card';

function headingsInOrder(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll('h1, h2, h3, h4, h5, h6'));
}

describe('CardTitle heading levels (UI-002)', () => {
  it('defaults to h3 and honors the level prop', () => {
    const { unmount } = render(<CardTitle>Default</CardTitle>);
    expect(screen.getByRole('heading', { level: 3, name: 'Default' })).toBeDefined();
    unmount();
    render(<CardTitle level={1}>Primary</CardTitle>);
    expect(screen.getByRole('heading', { level: 1, name: 'Primary' })).toBeDefined();
  });

  it('CardTitleEditorial also honors the level prop', () => {
    render(<CardTitleEditorial level={2}>Editorial</CardTitleEditorial>);
    expect(screen.getByRole('heading', { level: 2, name: 'Editorial' })).toBeDefined();
  });
});

describe('auth page heading order (UI-003)', () => {
  it('login renders exactly one h1 — "Sign in to LegacyFlow" — before any other heading', () => {
    const { container } = render(<LoginPage />);
    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]?.textContent).toMatch(/sign in to legacyflow/i);
    const headings = headingsInOrder(container);
    expect(headings[0]?.tagName).toBe('H1');
  });

  it('signup renders exactly one h1 describing account creation, first in DOM order', () => {
    const { container } = render(<SignUpPage />);
    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]?.textContent).toMatch(/create your legacyflow account/i);
    const headings = headingsInOrder(container);
    expect(headings[0]?.tagName).toBe('H1');
  });
});
