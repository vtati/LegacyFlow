'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

/**
 * Segment error boundary (ERR-008). Render failures previously landed
 * on Next.js's default screen with no recovery path and no reference
 * for support. This keeps the app shell, explains the failure in plain
 * language, and offers a retry — saved answers persist server-side as
 * they are entered, so a retry never loses the family's work.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): React.JSX.Element {
  useEffect(() => {
    // Surface the failure in the browser console for support sessions;
    // the digest below is the reference support can trace end-to-end.
    console.error(error);
  }, [error]);

  return (
    <main id="main-content" className="mx-auto flex min-h-[60vh] w-full max-w-xl flex-col justify-center px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle>Something went wrong</CardTitle>
          <CardDescription>
            This page hit an unexpected problem and couldn&rsquo;t finish loading.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-lf-text-muted">
            Don&rsquo;t worry — your saved answers and documents are safe. Everything you&rsquo;ve
            entered is stored as you go, so trying again won&rsquo;t lose your progress.
          </p>
          {error.digest && (
            <p className="text-xs text-lf-text-subtle">
              Reference: {error.digest} — quote this if you contact support.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="button" onClick={() => reset()}>
              Try again
            </Button>
            <Button type="button" variant="outline" asChild>
              <Link href="/">Go to the home page</Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
