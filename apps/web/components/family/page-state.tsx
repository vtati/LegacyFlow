'use client';

import { AlertTriangle, Inbox } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

/** Skeleton rows for data-loading states. */
export function LoadingState({
  lines = 4,
  label = 'Loading…',
}: {
  lines?: number;
  label?: string;
}) {
  return (
    <div role="status" aria-label={label} className="space-y-3">
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          className={cn(
            'animate-pulse rounded-lf-card bg-lf-canvas-subtle',
            i === 0 ? 'h-8 w-1/3' : 'h-16 w-full',
          )}
          aria-hidden="true"
        />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  );
}

interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
}

/** Friendly error state; 403 renders the "ask your firm" permission-denied copy. */
export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const denied = error instanceof ApiError && error.isPermissionDenied;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-lf-control bg-lf-error-100 text-lf-error-700">
            <AlertTriangle className="size-5" aria-hidden="true" />
          </span>
          <div>
            <CardTitle>{denied ? 'Access needed' : 'Something went wrong'}</CardTitle>
            <CardDescription>
              {denied
                ? 'Your firm has not granted access to this area yet.'
                : 'We could not load this page. Your information is safe.'}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-lf-text-muted">
          {denied
            ? 'Ask your firm to invite you or check which household member has access, then try again.'
            : error instanceof Error
              ? error.message
              : 'Please try again in a moment.'}
        </p>
        {onRetry && (
          <Button variant="outline" onClick={onRetry}>
            Try again
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

interface EmptyStateProps {
  title: string;
  description: string;
  action?: React.ReactNode;
  /**
   * `aurora` renders the Lumen Aurora treatment (aurora field wash +
   * gradient medallion) — opt-in per surface (Phase 3 family surfaces).
   * The default `plain` treatment is unchanged; professional pages share
   * this component and keep it.
   */
  variant?: 'plain' | 'aurora';
}

/** Empty state with optional call-to-action. */
export function EmptyState({ title, description, action, variant = 'plain' }: EmptyStateProps) {
  if (variant === 'aurora') {
    return (
      <Card className="relative overflow-hidden">
        <div aria-hidden="true" className="absolute inset-0 bg-lf-aurora" />
        <CardContent className="relative flex flex-col items-center gap-3 px-6 py-12 text-center">
          <span className="flex size-11 items-center justify-center rounded-full bg-lf-gradient-action text-white shadow-lf-glow">
            <Inbox className="size-5" aria-hidden="true" />
          </span>
          <p className="font-lf-serif text-lg font-medium text-lf-text">{title}</p>
          <p className="max-w-md text-sm text-lf-text-muted">{description}</p>
          {action}
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-3 px-6 py-12 text-center">
        <span className="flex size-11 items-center justify-center rounded-full bg-lf-accent-100 text-lf-accent-700">
          <Inbox className="size-5" aria-hidden="true" />
        </span>
        <p className="font-lf-serif text-lg font-medium text-lf-text">{title}</p>
        <p className="max-w-md text-sm text-lf-text-muted">{description}</p>
        {action}
      </CardContent>
    </Card>
  );
}
