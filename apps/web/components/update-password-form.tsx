'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react';
import { getAuthAdapter } from '@/lib/auth';
import { passwordSchema } from './signup-form';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

const schema = z
  .object({
    password: passwordSchema,
    confirmPassword: z.string().min(1, 'Please confirm your new password.'),
  })
  .refine((values) => values.password === values.confirmPassword, {
    message: 'Passwords do not match.',
    path: ['confirmPassword'],
  });

type Values = z.infer<typeof schema>;

/**
 * Set-a-new-password form. Reached via the recovery link
 * (/auth/callback?next=/auth/update-password), which establishes a recovery
 * session before this page renders.
 */
export function UpdatePasswordForm(): React.JSX.Element {
  const [sessionState, setSessionState] = useState<'checking' | 'ready' | 'missing'>('checking');
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  useEffect(() => {
    let cancelled = false;
    getAuthAdapter()
      .getSession()
      .then((session) => {
        if (!cancelled) setSessionState(session ? 'ready' : 'missing');
      })
      .catch(() => {
        if (!cancelled) setSessionState('missing');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit(values: Values): Promise<void> {
    setError(null);
    try {
      await getAuthAdapter().updatePassword(values.password);
      setDone(true);
      window.setTimeout(() => {
        window.location.href = '/family/home';
      }, 1500);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not update the password. Please try again.',
      );
    }
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle className="text-2xl">Choose a new password</CardTitle>
        <CardDescription>Enter your new password below.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {error && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-lf-control border border-lf-error-300 bg-lf-error-50 px-3 py-2 text-sm text-lf-error-700"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </p>
        )}

        {sessionState === 'checking' && (
          <p role="status" className="flex items-center gap-2 text-sm text-lf-text-muted">
            <Loader2 className="animate-spin" aria-hidden="true" /> Verifying your reset link…
          </p>
        )}

        {sessionState === 'missing' && (
          <div className="flex flex-col items-center gap-2 text-center">
            <p className="text-sm text-lf-text">
              This reset link is invalid or has expired. Reset links expire after 1 hour.
            </p>
            <Button asChild>
              <Link href="/auth/reset-password">Request a new reset link</Link>
            </Button>
          </div>
        )}

        {sessionState === 'ready' &&
          (done ? (
            <div
              role="status"
              className="flex flex-col items-center gap-2 rounded-lf-control border border-lf-success-300 bg-lf-success-50 px-4 py-6 text-center"
            >
              <CheckCircle2 className="size-8 text-lf-success-700" aria-hidden="true" />
              <p className="text-sm font-medium text-lf-text">Password updated</p>
              <p className="text-sm text-lf-text-muted">Taking you to your dashboard…</p>
            </div>
          ) : (
            <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="update-password">New password</Label>
                <Input
                  id="update-password"
                  type="password"
                  autoComplete="new-password"
                  placeholder="At least 8 characters"
                  aria-invalid={errors.password ? 'true' : undefined}
                  aria-describedby={errors.password ? 'update-password-error' : undefined}
                  {...register('password')}
                />
                {errors.password && (
                  <p id="update-password-error" role="alert" className="text-xs text-lf-error-700">
                    {errors.password.message}
                  </p>
                )}
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="update-confirm">Confirm new password</Label>
                <Input
                  id="update-confirm"
                  type="password"
                  autoComplete="new-password"
                  placeholder="Repeat your new password"
                  aria-invalid={errors.confirmPassword ? 'true' : undefined}
                  aria-describedby={errors.confirmPassword ? 'update-confirm-error' : undefined}
                  {...register('confirmPassword')}
                />
                {errors.confirmPassword && (
                  <p id="update-confirm-error" role="alert" className="text-xs text-lf-error-700">
                    {errors.confirmPassword.message}
                  </p>
                )}
              </div>
              <Button type="submit" disabled={isSubmitting} className="w-full">
                {isSubmitting && <Loader2 className="animate-spin" aria-hidden="true" />}
                {isSubmitting ? 'Updating…' : 'Update password'}
              </Button>
            </form>
          ))}
      </CardContent>
    </Card>
  );
}
