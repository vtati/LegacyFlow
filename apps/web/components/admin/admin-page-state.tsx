'use client';

import { ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorState } from '@/components/family/page-state';
import { ApiError } from '@/lib/api-client';

interface AdminErrorStateProps {
  error: unknown;
  onRetry?: () => void;
}

/**
 * Admin-aware error state. A 403 on an admin endpoint means the caller is
 * not a firm_admin for their firm, so say exactly that instead of the
 * generic family-workspace copy. All other errors delegate to ErrorState.
 */
export function AdminErrorState({ error, onRetry }: AdminErrorStateProps) {
  const denied = error instanceof ApiError && error.isPermissionDenied;
  if (!denied) {
    return <ErrorState error={error} onRetry={onRetry} />;
  }
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-lf-control bg-lf-error-100 text-lf-error-700">
            <ShieldAlert className="size-5" aria-hidden="true" />
          </span>
          <div>
            <CardTitle>Requires firm admin</CardTitle>
            <CardDescription>
              This admin area is limited to administrators of your firm.
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-lf-text-muted">
          Your account does not have the firm_admin role for your firm. Ask a firm administrator
          to grant you access, then try again.
        </p>
        {onRetry && (
          <Button type="button" variant="outline" className="min-h-[44px]" onClick={onRetry}>
            Try again
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Honest scope banner: the /admin/* API is firm_admin-scoped to the
 * caller's firm. Render this on admin pages so nobody mistakes the
 * contents for platform-wide tenant data.
 */
export function FirmScopeNote({ children }: { children?: React.ReactNode }) {
  return (
    <p className="rounded-lf-control border border-lf-border bg-lf-canvas-subtle px-4 py-3 text-sm text-lf-text-muted">
      <span className="font-medium text-lf-text">Firm-scoped admin.</span>{' '}
      {children ??
        'Everything on this page belongs to your firm only — never platform-wide data.'}
    </p>
  );
}
