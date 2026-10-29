'use client';

import { useCallback, useRef, type KeyboardEvent } from 'react';

/**
 * Roving-tabindex tab-strip keyboard pattern (UI-017, WAI-ARIA tabs):
 * only the selected tab is in the tab order; ArrowRight/ArrowLeft move
 * selection (wrapping) and focus together, Home/End jump to the ends.
 * Attach `onKeyDown` to the tablist, `refFor(i)` + `tabIndexFor(i)` to
 * each tab button.
 */
export function useRovingTabs(
  count: number,
  activeIndex: number,
  onChange: (index: number) => void,
): {
  onKeyDown: (event: KeyboardEvent) => void;
  refFor: (index: number) => (element: HTMLElement | null) => void;
  tabIndexFor: (index: number) => 0 | -1;
} {
  const refs = useRef<Array<HTMLElement | null>>([]);

  const refFor = useCallback(
    (index: number) =>
      (element: HTMLElement | null): void => {
        refs.current[index] = element;
      },
    [],
  );

  const tabIndexFor = useCallback(
    (index: number): 0 | -1 => (index === activeIndex ? 0 : -1),
    [activeIndex],
  );

  const onKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (count === 0) return;
      let next: number | null = null;
      switch (event.key) {
        case 'ArrowRight':
          next = (activeIndex + 1) % count;
          break;
        case 'ArrowLeft':
          next = (activeIndex - 1 + count) % count;
          break;
        case 'Home':
          next = 0;
          break;
        case 'End':
          next = count - 1;
          break;
        default:
          return;
      }
      event.preventDefault();
      onChange(next);
      // The tab buttons persist across selection changes, so focus can
      // move immediately — it lands on the newly selected tab.
      refs.current[next]?.focus();
    },
    [count, activeIndex, onChange],
  );

  return { onKeyDown, refFor, tabIndexFor };
}
