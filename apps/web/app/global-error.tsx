'use client';

import '@legacyflow/design-tokens/tokens.css';
import './globals.css';

/**
 * Root error boundary (ERR-008): replaces the whole document when the
 * root layout itself fails, so it must render its own <html>/<body> and
 * import the design tokens itself (the root layout is not in the tree).
 * Same recovery contract as app/error.tsx: plain language, a retry, a
 * way home, reassurance that saved answers persist, and the digest as
 * the support reference.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): React.JSX.Element {
  return (
    <html lang="en">
      <body className="bg-lf-canvas text-lf-text">
        <main className="mx-auto flex min-h-screen w-full max-w-xl flex-col justify-center px-4 py-12">
          <div className="rounded-lf-card border border-lf-border bg-lf-canvas-elevated p-6 shadow-lf-card">
            <h1 className="font-lf-serif text-2xl font-semibold tracking-tight">
              Something went wrong
            </h1>
            <p className="mt-2 text-sm text-lf-text-muted">
              LegacyFlow hit an unexpected problem and couldn&rsquo;t load.
            </p>
            <p className="mt-4 text-sm text-lf-text-muted">
              Don&rsquo;t worry — your saved answers and documents are safe. Everything you&rsquo;ve
              entered is stored as you go, so trying again won&rsquo;t lose your progress.
            </p>
            {error.digest && (
              <p className="mt-4 text-xs text-lf-text-subtle">
                Reference: {error.digest} — quote this if you contact support.
              </p>
            )}
            <div className="mt-6 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => reset()}
                className="inline-flex h-10 items-center justify-center rounded-lf-control bg-lf-primary-900 px-4 py-2 text-sm font-medium text-lf-primary-foreground hover:bg-lf-primary-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-accent-500"
              >
                Try again
              </button>
              {/* A plain anchor: the router may be part of what failed. */}
              <a
                href="/"
                className="inline-flex h-10 items-center justify-center rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-4 py-2 text-sm font-medium text-lf-text hover:bg-lf-canvas focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-accent-500"
              >
                Go to the home page
              </a>
            </div>
          </div>
        </main>
      </body>
    </html>
  );
}
