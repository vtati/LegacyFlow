import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';
import type { AuthSession } from './adapter';
import { MOCK_SESSION_COOKIE } from './constants';
import { isMockAuth } from './adapter';

/**
 * Server-side session read. Mock mode: parse the `lf_mock_session` cookie.
 * Real mode: validate with Supabase (never trust the cookie payload alone).
 */
export async function getServerSession(): Promise<AuthSession | null> {
  const cookieStore = await cookies();

  if (isMockAuth()) {
    const raw = cookieStore.get(MOCK_SESSION_COOKIE)?.value;
    if (!raw) return null;
    try {
      const session = JSON.parse(decodeURIComponent(raw)) as AuthSession;
      if (!session?.user?.id) return null;
      if (new Date(session.expiresAt).getTime() < Date.now()) return null;
      return session;
    } catch {
      return null;
    }
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
