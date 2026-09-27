'use client';

import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { AlertTriangle, FlaskConical, Loader2, MailCheck } from 'lucide-react';
import { getAuthAdapter, isMockAuth } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

const magicLinkSchema = z.object({
  email: z.string().min(1, 'Email is required.').email('Enter a valid email address.'),
});

type MagicLinkValues = z.infer<typeof magicLinkSchema>;

function GoogleIcon(): React.JSX.Element {
  // Simple "G" mark — placeholder brand glyph for the OAuth button.
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden="true">
      <path
        fill="currentColor"
        d="M21.35 11.1H12v2.9h5.35c-.5 2.4-2.55 3.5-5.35 3.5a5.9 5.9 0 1 1 0-11.8c1.5 0 2.85.55 3.9 1.45l2.1-2.1A8.9 8.9 0 1 0 12 20.9c4.45 0 8.4-3.2 8.4-8.9 0-.3-.05-.6-.05-.9Z"
      />
    </svg>
  );
}

/**
 * Login form (§5.6): Google OAuth button + magic-link email form.
 * Works with both adapters; mock mode is labeled prominently.
 */
export function LoginForm(): React.JSX.Element {
  const mock = isMockAuth();
  const [googleLoading, setGoogleLoading] = useState(false);
  const [magicSent, setMagicSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<MagicLinkValues>({ resolver: zodResolver(magicLinkSchema) });

  function nextPath(): string {
    if (typeof window === 'undefined') return '/family/home';
    return new URLSearchParams(window.location.search).get('next') ?? '/family/home';
  }

  async function handleGoogle(): Promise<void> {
    setGoogleLoading(true);
    setError(null);
    try {
      await getAuthAdapter().signInWithGoogle();
      if (mock) {
        // Mock adapter signs in locally — no OAuth redirect happens.
        window.location.href = nextPath();
      }
      // Real adapter redirects to the provider; nothing more to do here.
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Google sign-in failed. Please try again.');
      setGoogleLoading(false);
    }
  }

  async function handleMagicLink(values: MagicLinkValues): Promise<void> {
    setError(null);
    try {
      await getAuthAdapter().signInWithMagicLink(values.email);
      setMagicSent(true);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not send the magic link. Please try again.',
      );
    }
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle className="text-2xl">Sign in to LegacyFlow</CardTitle>
        <CardDescription>Use Google or a magic link sent to your email.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {mock && (
          <p
            role="note"
            className="flex items-start gap-2 rounded-lf-control border border-lf-accent-300 bg-lf-accent-50 px-3 py-2 text-xs text-lf-accent-900"
          >
            <FlaskConical className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>
              <strong>Local dev mock auth — no real Supabase configured.</strong> Sign-in creates a
              synthetic demo user stored in a local cookie. Set NEXT_PUBLIC_SUPABASE_URL and
              NEXT_PUBLIC_SUPABASE_ANON_KEY for real auth.
            </span>
          </p>
        )}

        {error && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-lf-control border border-lf-error-300 bg-lf-error-50 px-3 py-2 text-sm text-lf-error-700"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </p>
        )}

        <Button
          onClick={handleGoogle}
          disabled={googleLoading}
          aria-label="Continue with Google"
          className="w-full"
        >
          {googleLoading ? <Loader2 className="animate-spin" aria-hidden="true" /> : <GoogleIcon />}
          {googleLoading ? 'Connecting…' : 'Continue with Google'}
        </Button>

        <div className="flex items-center gap-3" aria-hidden="true">
          <span className="h-px flex-1 bg-lf-border" />
          <span className="text-xs text-lf-text-subtle">or</span>
          <span className="h-px flex-1 bg-lf-border" />
        </div>

        {magicSent ? (
          <div
            role="status"
            className="flex flex-col items-center gap-2 rounded-lf-control border border-lf-success-300 bg-lf-success-50 px-4 py-6 text-center"
          >
            <MailCheck className="size-8 text-lf-success-700" aria-hidden="true" />
            <p className="text-sm font-medium text-lf-text">Check your email</p>
            <p className="text-sm text-lf-text-muted">
              {mock ? (
                <>
                  Magic link sent <strong>(mock)</strong> — in local dev, use “Continue with Google”
                  to sign in as the synthetic demo user instead.
                </>
              ) : (
                'We sent you a sign-in link. It expires in 15 minutes.'
              )}
            </p>
            <Button variant="ghost" size="sm" onClick={() => setMagicSent(false)}>
              Use a different email
            </Button>
          </div>
        ) : (
          <form onSubmit={handleSubmit(handleMagicLink)} noValidate className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="magic-email">Email address</Label>
              <Input
                id="magic-email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                aria-invalid={errors.email ? 'true' : undefined}
                aria-describedby={errors.email ? 'magic-email-error' : undefined}
                {...register('email')}
              />
              {errors.email && (
                <p id="magic-email-error" role="alert" className="text-xs text-lf-error-700">
                  {errors.email.message}
                </p>
              )}
            </div>
            <Button type="submit" variant="outline" disabled={isSubmitting} className="w-full">
              {isSubmitting && <Loader2 className="animate-spin" aria-hidden="true" />}
              {isSubmitting ? 'Sending…' : 'Email me a sign-in link'}
            </Button>
          </form>
        )}

        <p className="text-center text-xs text-lf-text-subtle">
          By signing in you agree to the Terms and Privacy Policy. Demo scaffold — attorney-pending.
        </p>
      </CardContent>
    </Card>
  );
}
