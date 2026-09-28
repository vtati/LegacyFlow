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

  return (
    <p
      role="status"
      className={cn(
        'hidden items-center gap-1.5 text-xs md:flex',
        snapshot.status === 'error' ? 'text-lf-error-700' : 'text-lf-text-subtle',
      )}
    >
      {snapshot.status === 'saving' && (
        <>
          <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          Saving…
        </>
      )}
      {snapshot.status === 'saved' && (
        <>
          <CheckCheck className="size-4 text-lf-success-700" aria-hidden="true" />
          All changes saved
        </>
      )}
      {snapshot.status === 'error' && (
        <>
          <CloudOff className="size-4" aria-hidden="true" />
          Couldn&rsquo;t save — try again
        </>
      )}
    </p>
  );
}
