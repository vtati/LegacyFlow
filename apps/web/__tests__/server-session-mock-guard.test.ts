/**
 * SEC-001 residual: getServerSession() had no production guard — in
 * mock mode (Supabase env unset) it returned the parsed lf_mock_session
 * cookie session regardless of NODE_ENV, so a forged cookie was a
 * session in production wherever middleware did not run first (the
 * (app) layout gates /attorney/* on getServerSession alone). In
 * production a mock cookie must never be honoured; outside production
 * the mock behaviour (user-id + unexpired expiresAt) is unchanged.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MOCK_SESSION_COOKIE } from '../lib/auth/constants';

const cookieStore = vi.hoisted(() => ({ value: undefined as string | undefined }));

vi.mock('next/headers', () => ({
  cookies: async () => ({
    get: (name: string) =>
      name === MOCK_SESSION_COOKIE && cookieStore.value
        ? { name, value: cookieStore.value }
        : undefined,
    getAll: () => [],
    set: () => {},
  }),
}));

import { getServerSession } from '../lib/auth/server';

function validCookie(): string {
  return encodeURIComponent(
    JSON.stringify({
      user: { id: 'user-1', email: 'sample@example.invalid', displayName: 'Sample', provider: 'mock' },
      expiresAt: '2999-01-01T00:00:00.000Z',
    }),
  );
}

afterEach(() => {
  vi.unstubAllEnvs();
  cookieStore.value = undefined;
});

describe('getServerSession mock mode (SEC-001 residual)', () => {
  it('never honours a mock cookie in production, even a valid one', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    cookieStore.value = validCookie();
    await expect(getServerSession()).resolves.toBeNull();
  });

  it('still returns a valid mock session outside production', async () => {
    vi.stubEnv('NODE_ENV', 'development');
    cookieStore.value = validCookie();
    const session = await getServerSession();
    expect(session?.user.id).toBe('user-1');
  });
});
