'use client';

import { useEffect, useRef, useState } from 'react';

interface CountUpProps {
  /** Final value to count to. */
  end: number;
  prefix?: string;
  suffix?: string;
  /** Rendered before JS runs / for reduced-motion users. */
  fallback: string;
  className?: string;
}

/**
 * Animated number counter (Lumen Aurora motion catalog).
 * Counts once when scrolled into view; reduced-motion and no-IO
 * environments keep the static fallback text.
 */
export function CountUp({ end, prefix = '', suffix = '', fallback, className }: CountUpProps): React.JSX.Element {
  const ref = useRef<HTMLSpanElement>(null);
  const [label, setLabel] = useState(fallback);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let raf = 0;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries[0].isIntersecting) return;
        observer.disconnect();
        const duration = 1300;
        const start = performance.now();
        const tick = (now: number) => {
          const t = Math.min(1, (now - start) / duration);
          const eased = 1 - Math.pow(1 - t, 3);
          setLabel(`${prefix}${Math.round(end * eased).toLocaleString('en-US')}${suffix}`);
          if (t < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      },
      { threshold: 0.4 },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [end, prefix, suffix]);

  return (
    <span ref={ref} className={className}>
      {label}
    </span>
  );
}

export default CountUp;
