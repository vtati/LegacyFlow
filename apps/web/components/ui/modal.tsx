'use client';

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useFocusTrap } from '@/lib/hooks/use-focus-trap';
import { cn } from '@/lib/utils';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
}

/**
 * Accessible modal dialog: Escape closes, backdrop click closes, focus lands
 * on the title when the dialog opens, focus is trapped inside while open,
 * and focus returns to the element that opened the dialog when it closes.
 * Body scroll is locked while open.
 */
export function Modal({ open, onClose, title, description, children }: ModalProps) {
  // Portaled to <body> (see mobile-nav.tsx): a `position: fixed` overlay
  // must not depend on having no filtered/transformed ancestor.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = `modal-title-${title.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`;

  // Focus trap / Escape / scroll-lock / focus-return live in the shared
  // hook (UI-004) — the title (tabIndex={-1}) is the initial-focus
  // element and counts as the first trap boundary.
  useFocusTrap(open, dialogRef, { onEscape: onClose, initialFocusRef: titleRef });

  if (!open || !mounted) return null;

  return createPortal(
    <>
      <style>{`@keyframes lf-scrim-in{from{opacity:0}to{opacity:1}}@keyframes lf-modal-in{from{opacity:0;transform:scale(.96) translateY(6px)}to{opacity:1;transform:scale(1) translateY(0)}}`}</style>
      <div
        className="fixed inset-0 z-50 flex animate-[lf-scrim-in_200ms_ease-out] items-center justify-center overflow-y-auto bg-[rgb(6_12_22/0.45)] p-4"
        onClick={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      >
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          className={cn(
            'w-full max-w-lg animate-[lf-modal-in_200ms_ease-out] rounded-lf-lg border border-lf-border bg-lf-canvas-elevated shadow-lf-card',
          )}
        >
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle>
                <span id={titleId} ref={titleRef} tabIndex={-1} className="outline-none">
                  {title}
                </span>
              </CardTitle>
              {description && <CardDescription className="mt-1">{description}</CardDescription>}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close dialog"
              className="rounded-lf-control p-1.5 text-lf-text-subtle hover:bg-lf-canvas-subtle hover:text-lf-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-accent-500"
            >
              <X className="size-5" aria-hidden="true" />
            </button>
          </CardHeader>
          <div className="px-6 pb-6">{children}</div>
        </div>
      </div>
    </>,
    document.body,
  );
}
