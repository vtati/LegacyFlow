'use client';

import { cn } from '@/lib/utils';

interface ProgressBarProps {
  value: number;
  label?: string;
  className?: string;
}

/** Accessible progress bar with an sr-only percentage label fallback. */
export function ProgressBar({ value, label, className }: ProgressBarProps) {
  const clamped = Math.min(100, Math.max(0, Math.round(value)));
  const accessibleLabel = label ?? `Progress: ${clamped}% complete`;
  return (
    <div className={cn('w-full', className)}>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={clamped}
        aria-label={accessibleLabel}
        className="h-2 w-full overflow-hidden rounded-md bg-lf-accent-100"
      >
        <div
          className="h-full rounded-md bg-lf-accent-600 transition-[width] duration-300"
          style={{ width: `${clamped}%` }}
        />
      </div>
      <span className="sr-only">{accessibleLabel}</span>
    </div>
  );
}
