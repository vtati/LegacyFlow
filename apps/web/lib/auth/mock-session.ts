import type { AuthSession } from './adapter';

/**
 * Mock-session cookie validation, shared by the middleware and the
 * server session reader (SEC-001). A mock session is only ever a
 * local-dev convenience: the payload must be well-formed JSON carrying
 * a user id and an unexpired `expiresAt` — mere cookie presence proves
 * nothing.
 */
export function parseMockSessionCookie(
  raw: string | null | undefined,
  now: number = Date.now(),
): AuthSession | null {
  if (!raw) return null;
  try {
    const session = JSON.parse(decodeURIComponent(raw)) as AuthSession;
    if (!session?.user?.id) return null;
    const expires = new Date(session.expiresAt).getTime();
    if (Number.isNaN(expires) || expires < now) return null;
    return session;
  } catch {
    return null;
  }
}

export type MockAuthDecision = 'pass' | 'login' | 'misconfigured';

/**
 * Middleware decision for mock mode (Supabase env vars unset):
 * - production → 'misconfigured' (fail closed: mock mode must never
 *   run in production, whatever the cookie holds);
 * - otherwise a valid mock session passes, anything else goes to login.
 */
export function mockAuthDecision(options: {
  nodeEnv: string | undefined;
  cookieValue: string | null | undefined;
  now?: number;
}): MockAuthDecision {
  if (options.nodeEnv === 'production') return 'misconfigured';
  return parseMockSessionCookie(options.cookieValue, options.now) ? 'pass' : 'login';
}
