'use client';

import { useEffect, useState } from 'react';
import { CheckCheck, CloudOff, LoaderCircle } from 'lucide-react';
import { getSaveStatus, subscribeSaveStatus, type SaveSnapshot } from '@/lib/save-status';
import { cn } from '@/lib/utils';

/**
 * Header save indicator driven by the real save-status bus.
 * Hidden until the first save activity in the session — never claims
 * "saved" on pages where nothing was persisted.
 */
export function SaveIndicator(): React.JSX.Element | null {
  const [snapshot, setSnapshot] = useState<SaveSnapshot | null>(() => getSaveStatus());

  useEffect(() => subscribeSaveStatus(setSnapshot), []);

  if (!snapshot) return null;

  // UI-009: never `hidden` below md — mobile is the interview's primary
  // device and a failed save must be visible there. The error state
  // renders in full at every width; saving/saved get a compact mobile
  // presentation (icon in a small pill, label sr-only below md).
  return (
    <p
      role="status"
      className={cn(
        'flex items-center gap-1.5 rounded-full border border-lf-border bg-lf-canvas-elevated px-2.5 py-1 text-xs',
        snapshot.status === 'error' ? 'text-lf-error-700' : 'text-lf-text-subtle',
      )}
    >
      {snapshot.status === 'saving' && (
        <>
          <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          <span className="max-md:sr-only">Saving…</span>
        </>
      )}
      {snapshot.status === 'saved' && (
        <>
          <CheckCheck className="size-4 text-lf-success-700" aria-hidden="true" />
          <span className="max-md:sr-only">All changes saved</span>
        </>
      )}
      {snapshot.status === 'error' && (
        <>
          <CloudOff className="size-4" aria-hidden="true" />
          <span>Couldn&rsquo;t save — try again</span>
        </>
      )}
    </p>
  );
}
