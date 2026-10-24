'use client';

import { useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';

interface RevealProps {
  children: React.ReactNode;
  className?: string;
  /** Stagger delay in ms applied to the reveal transition. */
  delayMs?: number;
}

/**
 * Scroll-reveal wrapper (Lumen Aurora motion catalog).
 * Content renders visible by default; after mount the wrapper arms the
 * hidden state and an IntersectionObserver reveals it once on entry.
 * No-JS, pre-hydration, and reduced-motion users always see the content
 * (the CSS forces the armed state visible under prefers-reduced-motion).
 */
export function Reveal({ children, className, delayMs }: RevealProps): React.JSX.Element {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') return;
    el.classList.add('lf-reveal-armed');
    // Already in view at mount: reveal synchronously rather than relying
    // on the observer's first async delivery.
    const rect = el.getBoundingClientRect();
    if (rect.top < window.innerHeight && rect.bottom > 0) {
      el.classList.add('is-visible');
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            el.classList.add('is-visible');
            observer.disconnect();
          }
        }
      },
      { threshold: 0, rootMargin: '0px 0px -8% 0px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={cn('lf-reveal', className)}
      style={delayMs ? { transitionDelay: `${delayMs}ms` } : undefined}
    >
      {children}
    </div>
  );
}

export default Reveal;
