'use client';

import { TriangleAlert } from 'lucide-react';

/** Prominent draft watermark notice — required anywhere draft documents appear. */
export const WATERMARK_NOTICE = 'DRAFT — PENDING ATTORNEY REVIEW';

export function WatermarkNotice({ detail }: { detail?: string }): React.JSX.Element {
  return (
    <div
      role="note"
      aria-label="Draft watermark notice"
      className="flex items-start gap-3 rounded-lf-card border-2 border-lf-error-600 bg-lf-error-50 px-4 py-3"
    >
      <TriangleAlert className="mt-0.5 size-5 shrink-0 text-lf-error-700" aria-hidden="true" />
      <div>
        <p className="text-sm font-bold tracking-wide text-lf-error-700">{WATERMARK_NOTICE}</p>
        <p className="mt-1 text-sm text-lf-error-700">
          {detail ??
            'Every document generated here is a watermarked draft for review only. It is not a legal document and cannot be signed or filed until your attorney approves it.'}
        </p>
      </div>
    </div>
  );
}
