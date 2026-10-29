'use client';

import { useEffect, type RefObject } from 'react';

/**
 * Elements that can receive keyboard focus, in DOM order.
 * Used by the focus trap; excludes disabled and inert elements.
 */
export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface UseFocusTrapOptions {
  /** Called when Escape is pressed while the trap is active. */
  onEscape?: () => void;
  /**
   * Element that receives initial focus on activation. It may sit
   * outside the tabbable set (e.g. a heading with tabIndex={-1}); it is
   * still treated as the first boundary, so Shift+Tab from it wraps to
   * the last control instead of escaping.
   */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** Lock body scroll while active (default true). */
  lockScroll?: boolean;
}

/**
 * Shared focus trap for modal surfaces (UI-004): while `active`, Tab
 * wraps inside `containerRef`, Escape calls `onEscape`, focus lands on
 * the initial-focus element, body scroll is locked, and focus returns
 * to the element that had it before activation when the trap turns off.
 * Extracted from components/ui/modal.tsx so the mobile nav drawer gets
 * exactly the same behavior.
 */
export function useFocusTrap<T extends HTMLElement>(
  active: boolean,
  containerRef: RefObject<T | null>,
  options: UseFocusTrapOptions = {},
): void {
  const { onEscape, initialFocusRef, lockScroll = true } = options;

  useEffect(() => {
    if (!active) return;
    // Remember the trigger so focus can return to it on close.
    const previouslyFocused = document.activeElement as HTMLElement | null;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onEscape?.();
        return;
      }
      if (event.key === 'Tab') {
        // Trap Tab navigation inside the container.
        const container = containerRef.current;
        if (!container) return;
        const focusable = Array.from(
          container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
        ).filter((element) => element.offsetParent !== null);
        if (focusable.length === 0) {
          event.preventDefault();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const activeElement = document.activeElement;
        if (event.shiftKey) {
          if (activeElement === first || activeElement === initialFocusRef?.current) {
            event.preventDefault();
            last?.focus();
          }
        } else if (activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };

    document.addEventListener('keydown', onKeyDown);
    const previousOverflow = document.body.style.overflow;
    if (lockScroll) document.body.style.overflow = 'hidden';
    initialFocusRef?.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      if (lockScroll) document.body.style.overflow = previousOverflow;
      // Return focus to the trigger that opened the surface.
      previouslyFocused?.focus?.();
    };
  }, [active, containerRef, onEscape, initialFocusRef, lockScroll]);
}
