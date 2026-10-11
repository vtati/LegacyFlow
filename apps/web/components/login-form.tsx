'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
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
import { cn } from '@/lib/utils';

const magicLinkSchema = z.object({
  email: z.string().min(1, 'Email is required.').email('Enter a valid email address.'),
});

type MagicLinkValues = z.infer<typeof magicLinkSchema>;

const passwordSignInSchema = z.object({
  email: z.string().min(1, 'Email is required.').email('Enter a valid email address.'),
  password: z.string().min(1, 'Password is required.'),
});

type PasswordSignInValues = z.infer<typeof passwordSignInSchema>;

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

type Tab = 'password' | 'magic';

function nextPath(): string {
  if (typeof window === 'undefined') return '/family/home';
  return new URLSearchParams(window.location.search).get('next') ?? '/family/home';
}

/**
 * Login form (§5.6): Google OAuth button plus email sign-in with
 * password / magic-link tabs. Works with both adapters; mock mode is
 * labeled prominently.
 */
export function LoginForm(): React.JSX.Element {
  const mock = isMockAuth();
  const [tab, setTab] = useState<Tab>('password');
  const [googleLoading, setGoogleLoading] = useState(false);
  const [magicSent, setMagicSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const magicForm = useForm<MagicLinkValues>({ resolver: zodResolver(magicLinkSchema) });
  const passwordForm = useForm<PasswordSignInValues>({ resolver: zodResolver(passwordSignInSchema) });

  async function handleGoogle(): Promise<void> {
    setGoogleLoading(true);
    setError(null);
    try {
      await getAuthAdapter().signInWithGoogle();
      if (mock) {
        // Mock adapter signs in locally — no OAuth redirect happens.
        router.push(nextPath());
        router.refresh();
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

  async function handlePasswordSignIn(values: PasswordSignInValues): Promise<void> {
    setError(null);
    try {
      await getAuthAdapter().signInWithPassword(values.email, values.password);
      // Use client-side navigation so the freshly-set auth cookies are
      // visible to the middleware on the next request. A hard
      // window.location reload can race the cookie write and bounce the
      // user back to the marketing page.
      router.push(nextPath());
      router.refresh();
    } catch (err) {
      setError(
        err instanceof Error
          ? friendlyAuthError(err.message)
          : 'Could not sign in. Please check your email and password.',
      );
    }
  }

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle className="text-2xl">Sign in to LegacyFlow</CardTitle>
        <CardDescription>Use Google, your password, or a magic link sent to your email.</CardDescription>
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
              sample user stored in a local cookie. Set NEXT_PUBLIC_SUPABASE_URL and
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

        <div
          role="tablist"
          aria-label="Email sign-in method"
          className="grid grid-cols-2 gap-1 rounded-lf-control bg-lf-canvas p-1"
        >
          {(
            [
              { id: 'password', label: 'Password' },
              { id: 'magic', label: 'Magic link' },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => {
                setTab(t.id);
                setError(null);
              }}
              className={cn(
                'rounded-lf-control px-3 py-2 text-sm font-medium transition-colors',
                tab === t.id
                  ? 'bg-white text-lf-text shadow-sm'
                  : 'text-lf-text-muted hover:text-lf-text',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'password' ? (
          <form
            onSubmit={passwordForm.handleSubmit(handlePasswordSignIn)}
            noValidate
            className="flex flex-col gap-3"
            role="tabpanel"
            aria-label="Sign in with password"
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="password-email">Email address</Label>
              <Input
                id="password-email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                aria-invalid={passwordForm.formState.errors.email ? 'true' : undefined}
                aria-describedby={
                  passwordForm.formState.errors.email ? 'password-email-error' : undefined
                }
                {...passwordForm.register('email')}
              />
              {passwordForm.formState.errors.email && (
                <p id="password-email-error" role="alert" className="text-xs text-lf-error-700">
                  {passwordForm.formState.errors.email.message}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="password-password">Password</Label>
                <Link
                  href="/auth/reset-password"
                  className="text-xs font-medium text-lf-primary-700 underline-offset-2 hover:underline"
                >
                  Forgot password?
                </Link>
              </div>
              <Input
                id="password-password"
                type="password"
                autoComplete="current-password"
                placeholder="Your password"
                aria-invalid={passwordForm.formState.errors.password ? 'true' : undefined}
                aria-describedby={
                  passwordForm.formState.errors.password ? 'password-password-error' : undefined
                }
                {...passwordForm.register('password')}
              />
              {passwordForm.formState.errors.password && (
                <p id="password-password-error" role="alert" className="text-xs text-lf-error-700">
                  {passwordForm.formState.errors.password.message}
                </p>
              )}
            </div>
            <Button
              type="submit"
              disabled={passwordForm.formState.isSubmitting}
              className="w-full"
            >
              {passwordForm.formState.isSubmitting && (
                <Loader2 className="animate-spin" aria-hidden="true" />
              )}
              {passwordForm.formState.isSubmitting ? 'Signing in…' : 'Sign in'}
            </Button>
            <p className="text-center text-sm text-lf-text-muted">
              New to LegacyFlow?{' '}
              <Link
                href="/signup"
                className="font-medium text-lf-primary-700 underline-offset-2 hover:underline"
              >
                Create an account
              </Link>
            </p>
          </form>
        ) : magicSent ? (
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
                  to sign in as the sample user instead.
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
          <form
            onSubmit={magicForm.handleSubmit(handleMagicLink)}
            noValidate
            className="flex flex-col gap-3"
            role="tabpanel"
            aria-label="Sign in with magic link"
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="magic-email">Email address</Label>
              <Input
                id="magic-email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                aria-invalid={magicForm.formState.errors.email ? 'true' : undefined}
                aria-describedby={
                  magicForm.formState.errors.email ? 'magic-email-error' : undefined
                }
                {...magicForm.register('email')}
              />
              {magicForm.formState.errors.email && (
                <p id="magic-email-error" role="alert" className="text-xs text-lf-error-700">
                  {magicForm.formState.errors.email.message}
                </p>
              )}
            </div>
            <Button
              type="submit"
              variant="outline"
              disabled={magicForm.formState.isSubmitting}
              className="w-full"
            >
              {magicForm.formState.isSubmitting && (
                <Loader2 className="animate-spin" aria-hidden="true" />
              )}
              {magicForm.formState.isSubmitting ? 'Sending…' : 'Email me a sign-in link'}
            </Button>
          </form>
        )}

        <p className="text-center text-xs text-lf-text-subtle">
          By signing in you agree to the Terms and Privacy Policy. Attorney review pending.
        </p>
      </CardContent>
    </Card>
  );
}

/**
 * Supabase auth errors are technical ("Invalid login credentials"); map the
 * common ones to friendlier copy without leaking which half was wrong.
 */
function friendlyAuthError(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes('invalid login credentials')) {
    return 'Incorrect email or password. If you signed up with Google or a magic link, use that method instead — or reset your password.';
  }
  if (normalized.includes('email not confirmed')) {
    return 'Please confirm your email first — check your inbox for the confirmation link.';
  }
  if (normalized.includes('too many requests') || normalized.includes('rate limit')) {
    return 'Too many attempts. Please wait a little while and try again.';
  }
  return message;
}
