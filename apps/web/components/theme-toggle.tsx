'use client';

import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';

/**
 * Light (Atelier Light) / dark (Noir Desk) toggle.
 * Flips the `.dark` class on <html> — every surface follows via the
 * `--lf-*` token overrides in @legacyflow/design-tokens — and persists
 * the choice to localStorage (`lf-theme`), where the pre-paint init
 * script in the root layout picks it up on the next load.
 */
export function ThemeToggle(): React.JSX.Element {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains('dark'));
  }, []);

  const toggle = (): void => {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle('dark', next);
    try {
      localStorage.setItem('lf-theme', next ? 'dark' : 'light');
    } catch {
      // Storage unavailable (private mode) — the class flip still applies
      // for this session; the choice just won't persist.
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={isDark}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="flex size-10 items-center justify-center rounded-lf-control text-lf-text-muted transition-colors hover:bg-lf-canvas-subtle hover:text-lf-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-accent-500"
    >
      {/* Icons are CSS-driven off the `.dark` class so they are correct on
          first paint (no hydration flash); state only drives aria-pressed. */}
      <Moon className="size-5 dark:hidden" aria-hidden="true" />
      <Sun className="hidden size-5 dark:block" aria-hidden="true" />
    </button>
  );
}
