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

export const passwordSchema = z
  .string()
  .min(8, 'Password must be at least 8 characters.')
  .max(72, 'Password must be at most 72 characters.');

const signUpSchema = z
  .object({
    email: z.string().min(1, 'Email is required.').email('Enter a valid email address.'),
    password: passwordSchema,
    confirmPassword: z.string().min(1, 'Please confirm your password.'),
  })
  .refine((values) => values.password === values.confirmPassword, {
    message: 'Passwords do not match.',
    path: ['confirmPassword'],
  });

type SignUpValues = z.infer<typeof signUpSchema>;

/** Sign-up form: email + password account creation. */
export function SignUpForm(): React.JSX.Element {
  const mock = isMockAuth();
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SignUpValues>({ resolver: zodResolver(signUpSchema) });

  async function onSubmit(values: SignUpValues): Promise<void> {
    setError(null);
    try {
      const { needsConfirmation } = await getAuthAdapter().signUpWithPassword(
        values.email,
        values.password,
      );
      if (needsConfirmation) {
        setSent(true);
      } else {
        window.location.href = '/family/home';
      }
    } catch (err) {
      setError(
        err instanceof Error ? friendlySignUpError(err.message) : 'Could not create the account.',
      );
    }
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle className="text-2xl">Create your LegacyFlow account</CardTitle>
        <CardDescription>Use your email and a password — no Google account needed.</CardDescription>
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
              {mock
                ? 'Confirmation sent (mock).'
                : 'We sent a confirmation link. Follow it to activate your account, then sign in.'}
            </p>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/login">Back to sign in</Link>
            </Button>
          </div>
        ) : (
          <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="signup-email">Email address</Label>
              <Input
                id="signup-email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                aria-invalid={errors.email ? 'true' : undefined}
                aria-describedby={errors.email ? 'signup-email-error' : undefined}
                {...register('email')}
              />
              {errors.email && (
                <p id="signup-email-error" role="alert" className="text-xs text-lf-error-700">
                  {errors.email.message}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="signup-password">Password</Label>
              <Input
                id="signup-password"
                type="password"
                autoComplete="new-password"
                placeholder="At least 8 characters"
                aria-invalid={errors.password ? 'true' : undefined}
                aria-describedby={errors.password ? 'signup-password-error' : undefined}
                {...register('password')}
              />
              {errors.password && (
                <p id="signup-password-error" role="alert" className="text-xs text-lf-error-700">
                  {errors.password.message}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="signup-confirm">Confirm password</Label>
              <Input
                id="signup-confirm"
                type="password"
                autoComplete="new-password"
                placeholder="Repeat your password"
                aria-invalid={errors.confirmPassword ? 'true' : undefined}
                aria-describedby={errors.confirmPassword ? 'signup-confirm-error' : undefined}
                {...register('confirmPassword')}
              />
              {errors.confirmPassword && (
                <p id="signup-confirm-error" role="alert" className="text-xs text-lf-error-700">
                  {errors.confirmPassword.message}
                </p>
              )}
            </div>
            <Button type="submit" disabled={isSubmitting} className="w-full">
              {isSubmitting && <Loader2 className="animate-spin" aria-hidden="true" />}
              {isSubmitting ? 'Creating account…' : 'Create account'}
            </Button>
            <p className="text-center text-sm text-lf-text-muted">
              Already have an account?{' '}
              <Link
                href="/login"
                className="font-medium text-lf-primary-700 underline-offset-2 hover:underline"
              >
                Sign in
              </Link>
            </p>
          </form>
        )}

        <p className="text-center text-xs text-lf-text-subtle">
          By creating an account you agree to the Terms and Privacy Policy. Demo scaffold —
          attorney-pending.
        </p>
      </CardContent>
    </Card>
  );
}

function friendlySignUpError(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes('already registered') || normalized.includes('already exists')) {
    return 'An account with this email already exists. Try signing in instead.';
  }
  if (normalized.includes('password')) {
    return 'That password does not meet the requirements. Use at least 8 characters.';
  }
  if (normalized.includes('too many requests') || normalized.includes('rate limit')) {
    return 'Too many attempts. Please wait a little while and try again.';
  }
  return message;
}
