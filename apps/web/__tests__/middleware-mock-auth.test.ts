/**
 * SEC-001: mock-auth middleware failed open — when the Supabase env
 * vars were unset, ANY present `lf_mock_session` cookie value passed,
 * and there was no production guard at all. The middleware now
 * (a) never enters mock mode in production, and (b) in non-production
 * mock mode requires a well-formed, unexpired session payload (the
 * same validation getServerSession applies).
 */
import { describe, expect, it } from 'vitest';
import { mockAuthDecision, parseMockSessionCookie } from '../lib/auth/mock-session';
import { config } from '../middleware';

describe('middleware matcher (SEC-001 residual)', () => {
  it('covers every authenticated section, including /attorney', () => {
    // app/(app)/attorney/* sits under the authenticated layout; without
    // a matcher entry the middleware never ran for those routes.
    expect(config.matcher).toContain('/attorney/:path*');
    expect(config.matcher).toContain('/family/:path*');
    expect(config.matcher).toContain('/firm/:path*');
    expect(config.matcher).toContain('/admin/:path*');
  });
});

const NOW = new Date('2026-10-29T12:00:00Z').getTime();

function cookieFor(expiresAt: string, id: string | null = 'user-1'): string {
  return encodeURIComponent(
    JSON.stringify({
      user: { id, email: 'sample@example.invalid', displayName: 'Sample', provider: 'mock' },
      expiresAt,
    }),
  );
}

describe('parseMockSessionCookie (SEC-001)', () => {
  it('accepts a well-formed, unexpired session', () => {
    const session = parseMockSessionCookie(cookieFor('2026-10-30T12:00:00Z'), NOW);
    expect(session?.user.id).toBe('user-1');
  });

  it('rejects expired, malformed, and identity-less payloads', () => {
    expect(parseMockSessionCookie(cookieFor('2026-10-28T12:00:00Z'), NOW)).toBeNull();
    expect(parseMockSessionCookie('not-json', NOW)).toBeNull();
    expect(parseMockSessionCookie('{}', NOW)).toBeNull();
    expect(parseMockSessionCookie(cookieFor('2026-10-30T12:00:00Z', null), NOW)).toBeNull();
    expect(parseMockSessionCookie(cookieFor('not-a-date'), NOW)).toBeNull();
    expect(parseMockSessionCookie(undefined, NOW)).toBeNull();
    expect(parseMockSessionCookie('', NOW)).toBeNull();
  });
});

describe('mockAuthDecision (SEC-001)', () => {
  it('fails closed in production — even with a valid-looking cookie', () => {
    expect(
      mockAuthDecision({
        nodeEnv: 'production',
        cookieValue: cookieFor('2026-10-30T12:00:00Z'),
        now: NOW,
      }),
    ).toBe('misconfigured');
    expect(mockAuthDecision({ nodeEnv: 'production', cookieValue: undefined, now: NOW })).toBe(
      'misconfigured',
    );
  });

  it('passes a valid mock session outside production', () => {
    expect(
      mockAuthDecision({
        nodeEnv: 'development',
        cookieValue: cookieFor('2026-10-30T12:00:00Z'),
        now: NOW,
      }),
    ).toBe('pass');
  });

  it('sends missing/expired/garbage cookies to login outside production', () => {
    expect(mockAuthDecision({ nodeEnv: 'development', cookieValue: undefined, now: NOW })).toBe('login');
    expect(mockAuthDecision({ nodeEnv: 'test', cookieValue: 'anything-present', now: NOW })).toBe('login');
    expect(
      mockAuthDecision({
        nodeEnv: 'development',
        cookieValue: cookieFor('2026-10-28T12:00:00Z'),
        now: NOW,
      }),
    ).toBe('login');
  });
});
