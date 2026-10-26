'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

interface CoverageRingProps {
  /** Coverage percentage, 0–100 (clamped). */
  value: number;
  /** Outer diameter in px. */
  size?: number;
  /** Ring thickness in px. */
  strokeWidth?: number;
  /** Caption under the percentage (replaced by "Plan complete" at 100%). */
  caption?: string;
  /** Accessible label for the progressbar. */
  label?: string;
  className?: string;
}

const ANIMATION_MS = 600;

/**
 * Animated coverage ring (Lumen Aurora) — the plan filling with color.
 * The arc and the numeral count up together over 600ms ease-out whenever
 * the value changes; under prefers-reduced-motion the final state renders
 * immediately. The stroke is the signature gradient (graphic fill only —
 * the percentage text stays ink on the surface).
 */
export function CoverageRing({
  value,
  size = 152,
  strokeWidth = 12,
  caption = 'complete',
  label,
  className,
}: CoverageRingProps): React.JSX.Element {
  // Non-finite input degrades to 0 rather than propagating NaN into the
  // numeral, aria-valuenow, and strokeDashoffset.
  const safe = Number.isFinite(value) ? Math.round(value) : 0;
  const clamped = Math.min(100, Math.max(0, safe));
  // Reduced-motion users get the final state on first paint (no 0% flash).
  const [display, setDisplay] = useState(() => (prefersReducedMotion() ? clamped : 0));
  const displayRef = useRef(display);
  displayRef.current = display;
  const gradientId = useId().replace(/:/g, '');

  useEffect(() => {
    if (prefersReducedMotion()) {
      setDisplay(clamped);
      return;
    }
    // Count from wherever the ring currently sits, not from zero.
    const from = displayRef.current;
    if (from === clamped) return;
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / ANIMATION_MS);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(Math.round(from + (clamped - from) * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [clamped]);

  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - display / 100);
  const complete = clamped === 100;

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      aria-label={label ?? `Coverage: ${clamped}% complete`}
      className={cn('relative inline-flex shrink-0 items-center justify-center', className)}
      style={{ width: size, height: size }}
    >
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="-rotate-90"
        aria-hidden="true"
      >
        <defs>
          {/* Stops mirror --lf-gradient-primary in tokens.css (SVG stops
              cannot reference the CSS gradient itself). If the signature
              gradient is retuned, update these stops to match. */}
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="var(--lf-accent-400)" />
            <stop offset="45%" stopColor="var(--lf-accent-500)" />
            <stop offset="100%" stopColor="var(--lf-violet-500)" />
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--lf-border)"
          strokeWidth={strokeWidth}
        />
        <circle
          data-testid="coverage-ring-arc"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={`url(#${gradientId})`}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        <span className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">
          {display}%
        </span>
        {complete ? (
          <span className="mt-0.5 inline-flex items-center gap-1 text-xs font-medium text-lf-success-700">
            <Check className="size-3.5" aria-hidden="true" />
            Plan complete
          </span>
        ) : (
          <span className="mt-0.5 text-xs text-lf-text-muted">{caption}</span>
        )}
      </div>
    </div>
  );
}
