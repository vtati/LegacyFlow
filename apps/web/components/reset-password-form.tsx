'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { AlertTriangle, Loader2, MailCheck } from 'lucide-react';
import { getAuthAdapter, isMockAuth } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

const schema = z.object({
  email: z.string().min(1, 'Email is required.').email('Enter a valid email address.'),
});

type Values = z.infer<typeof schema>;

/** Forgot-password form: request a password-reset email. */
export function ResetPasswordForm(): React.JSX.Element {
  const mock = isMockAuth();
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  async function onSubmit(values: Values): Promise<void> {
    setError(null);
    try {
      await getAuthAdapter().sendPasswordReset(values.email);
      setSent(true);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not send the reset email. Please try again.',
      );
    }
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle className="text-2xl">Reset your password</CardTitle>
        <CardDescription>
          Enter your account email and we’ll send you a link to choose a new password.
        </CardDescription>
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

        {sent ? (
          <div
            role="status"
            className="flex flex-col items-center gap-2 rounded-lf-control border border-lf-success-300 bg-lf-success-50 px-4 py-6 text-center"
          >
            <MailCheck className="size-8 text-lf-success-700" aria-hidden="true" />
            <p className="text-sm font-medium text-lf-text">Check your email</p>
            <p className="text-sm text-lf-text-muted">
              {mock ? (
                <>Reset email sent <strong>(mock)</strong>.</>
              ) : (
                'If an account exists for that email, a reset link is on its way. It expires in 1 hour.'
              )}
            </p>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/login">Back to sign in</Link>
            </Button>
          </div>
        ) : (
          <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="reset-email">Email address</Label>
              <Input
                id="reset-email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                aria-invalid={errors.email ? 'true' : undefined}
                aria-describedby={errors.email ? 'reset-email-error' : undefined}
                {...register('email')}
              />
              {errors.email && (
                <p id="reset-email-error" role="alert" className="text-xs text-lf-error-700">
                  {errors.email.message}
                </p>
              )}
            </div>
            <Button type="submit" disabled={isSubmitting} className="w-full">
              {isSubmitting && <Loader2 className="animate-spin" aria-hidden="true" />}
              {isSubmitting ? 'Sending…' : 'Send reset link'}
            </Button>
            <p className="text-center text-sm text-lf-text-muted">
              Remembered it?{' '}
              <Link
                href="/login"
                className="font-medium text-lf-primary-700 underline-offset-2 hover:underline"
              >
                Back to sign in
              </Link>
            </p>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
