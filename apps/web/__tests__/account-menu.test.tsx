/** @vitest-environment jsdom */
/**
 * UI-023: closing the account menu with Escape must return focus to the
 * trigger button — otherwise keyboard focus is lost to <body>.
 */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AccountMenu } from '../components/account-menu';

const user = {
  id: 'user-1',
  email: 'ana@example.com',
  displayName: 'Ana Sharma',
  provider: 'email' as const,
};

describe('AccountMenu (UI-023)', () => {
  it('returns focus to the trigger when Escape closes the menu', async () => {
    const u = userEvent.setup();
    render(<AccountMenu user={user} />);
    const trigger = screen.getByRole('button', { name: /account menu for/i });
    await u.click(trigger);
    expect(screen.getByRole('menu')).toBeDefined();

    // Move focus inside the menu, as a keyboard user would.
    screen.getByRole('menuitem', { name: /sign out/i }).focus();
    expect(document.activeElement).not.toBe(trigger);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
