'use client';

import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
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
 * on the title when the dialog opens, body scroll is locked while open.
 */
export function Modal({ open, onClose, title, description, children }: ModalProps) {
  const titleRef = useRef<HTMLHeadingElement>(null);
  const titleId = `modal-title-${title.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`;

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    titleRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/60 p-4"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={cn(
          'w-full max-w-lg rounded-lf-card border border-lf-border bg-lf-canvas-elevated shadow-lf-card',
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
            className="rounded-lf-control p-1.5 text-lf-text-subtle hover:bg-lf-canvas-subtle hover:text-lf-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            <X className="size-5" aria-hidden="true" />
          </button>
        </CardHeader>
        <div className="px-6 pb-6">{children}</div>
      </div>
    </div>
  );
}
