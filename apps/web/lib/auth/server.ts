import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';
import type { AuthSession } from './adapter';
import { MOCK_SESSION_COOKIE } from './constants';
import { isMockAuth } from './adapter';
import { parseMockSessionCookie } from './mock-session';

/**
 * Server-side session read. Mock mode: parse the `lf_mock_session` cookie.
 * Real mode: validate with Supabase (never trust the cookie payload alone).
 */
export async function getServerSession(): Promise<AuthSession | null> {
  const cookieStore = await cookies();

  if (isMockAuth()) {
    // SEC-001 (fail closed, residual path): mock mode must never run in
    // production — mirror the middleware's 'misconfigured' posture and
    // honour no session, whatever the cookie holds. (Routes such as
    // /attorney/* reach this reader through the (app) layout, so the
    // guard cannot live in the middleware alone.)
    if (process.env.NODE_ENV === 'production') return null;
    // Shared with the middleware (SEC-001) so both surfaces apply the
    // exact same well-formed + unexpired validation.
    return parseMockSessionCookie(cookieStore.get(MOCK_SESSION_COOKIE)?.value);
  }

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) return null;

  const supabase = createServerClient(url, anonKey, {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: () => {
        // Read-only in this helper; middleware refreshes the session cookie.
      },
    },
  });
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;
  const rawProvider = user.app_metadata?.['provider'];
  return {
    user: {
      id: user.id,
      email: user.email ?? null,
      displayName: (user.user_metadata?.['full_name'] as string | undefined) ?? user.email ?? null,
      provider: rawProvider === 'google' ? 'google' : 'email',
    },
    expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  };
}
