/** @vitest-environment jsdom */
/**
 * UI-004: the mobile nav drawer is a modal dialog — Tab must wrap
 * inside it while open (the shared Modal already traps; the drawer did
 * not), Escape closes, focus lands in the drawer on open and returns to
 * the trigger on close.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MobileNav } from '../components/mobile-nav';

// jsdom has no layout, so offsetParent is always null and the trap's
// visibility filter would find nothing. Give every element a parent so
// focusable elements register as visible for this file's tests.
let originalOffsetParent: PropertyDescriptor | undefined;
beforeAll(() => {
  originalOffsetParent = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetParent');
  Object.defineProperty(HTMLElement.prototype, 'offsetParent', {
    configurable: true,
    get() {
      return this.parentElement;
    },
  });
});
afterAll(() => {
  if (originalOffsetParent) {
    Object.defineProperty(HTMLElement.prototype, 'offsetParent', originalOffsetParent);
  }
});

function focusablesIn(dialog: HTMLElement): HTMLElement[] {
  return Array.from(
    dialog.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]):not([tabindex="-1"]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

function backdropIn(dialog: HTMLElement): HTMLElement {
  const backdrop = dialog.querySelector<HTMLElement>('button.absolute.inset-0');
  if (!backdrop) throw new Error('backdrop button not found');
  return backdrop;
}

describe('MobileNav focus trap (UI-004)', () => {
  it('traps Tab inside the open drawer, closes on Escape, and returns focus', async () => {
    const user = userEvent.setup();
    render(<MobileNav section="family" />);
    const trigger = screen.getByRole('button', { name: 'Open navigation menu' });
    await user.click(trigger);

    const dialog = await screen.findByRole('dialog');
    // Initial focus lands in the drawer (the close button).
    expect(dialog.contains(document.activeElement)).toBe(true);

    const focusables = focusablesIn(dialog);
    expect(focusables.length).toBeGreaterThan(2);
    const first = focusables[0]!;
    const last = focusables[focusables.length - 1]!;

    // Tab on the last element wraps to the first.
    last.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(first);

    // Shift+Tab on the first element wraps to the last.
    first.focus();
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(last);

    // Escape closes and focus returns to the trigger.
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('keeps the invisible backdrop out of the tab order but clickable', async () => {
    const user = userEvent.setup();
    render(<MobileNav section="family" />);
    await user.click(screen.getByRole('button', { name: 'Open navigation menu' }));

    const dialog = await screen.findByRole('dialog');
    const backdrop = backdropIn(dialog);
    // Out of the tab order — Tab must never land on an invisible stop.
    expect(backdrop.tabIndex).toBe(-1);
    expect(focusablesIn(dialog)).not.toContain(backdrop);

    // The trap's wrap targets skip it too: Tab on the last control
    // wraps to the first *visible* control (the close button).
    const focusables = focusablesIn(dialog);
    const first = focusables[0]!;
    const last = focusables[focusables.length - 1]!;
    expect(first).not.toBe(backdrop);
    last.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(first);
    expect(document.activeElement).not.toBe(backdrop);

    // Click-to-close still works.
    fireEvent.click(backdrop);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
