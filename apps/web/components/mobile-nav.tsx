'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { usePathname } from 'next/navigation';
import { Menu, X } from 'lucide-react';
import { PRODUCT_NAME } from '@/lib/constants';
import { SECTION_TITLES, type AppSection } from '@/lib/nav';
import { SectionNav } from '@/components/section-nav';

interface MobileNavProps {
  section: AppSection;
}

/**
 * Mobile navigation: a hamburger button (header, left) that opens the section
 * nav in a left slide-over drawer. Rendered only below the md breakpoint —
 * desktop keeps the permanent sidebar.
 *
 * The drawer is portaled to <body>: it is `position: fixed`, and the glass
 * header's backdrop-filter makes the header a containing block for fixed
 * descendants — rendered in place, the drawer collapsed into the 64px header
 * box and could not open (Phase 2 regression, fixed 2026-10-24).
 */
export function MobileNav({ section }: MobileNavProps): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => setOpen(false), []);

  // Close the drawer whenever the route changes (a nav link was followed).
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // While open: close on Escape, lock body scroll, move focus into the dialog.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
      triggerRef.current?.focus();
    };
  }, [open, close]);

  return (
    <div className="md:hidden">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open navigation menu"
        aria-expanded={open}
        className="flex size-10 items-center justify-center rounded-lg text-lf-text-muted transition-colors hover:bg-lf-canvas-subtle hover:text-lf-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
      >
        <Menu className="size-5" aria-hidden="true" />
      </button>

      {open
        ? createPortal(
            <div
          className="fixed inset-0 z-50"
          role="dialog"
          aria-modal="true"
          aria-label={`${SECTION_TITLES[section]} navigation`}
        >
          <button
            type="button"
            aria-label="Close navigation menu"
            onClick={close}
            className="absolute inset-0 cursor-default bg-lf-text/40"
          />
          <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-lf-canvas-elevated shadow-lf-subtle">
            <div className="flex items-center justify-between border-b border-lf-border px-4 py-3">
              <span className="font-lf-serif text-base font-semibold tracking-tight text-lf-primary-950">
                {PRODUCT_NAME}
              </span>
              <button
                ref={closeRef}
                type="button"
                onClick={close}
                aria-label="Close navigation menu"
                className="flex size-10 items-center justify-center rounded-lg text-lf-text-muted transition-colors hover:bg-lf-canvas-subtle hover:text-lf-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              >
                <X className="size-5" aria-hidden="true" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3">
              <p className="mb-3 px-3 text-xs font-semibold uppercase tracking-wider text-lf-text-subtle">
                {SECTION_TITLES[section]}
              </p>
              <SectionNav section={section} />
            </div>
          </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
