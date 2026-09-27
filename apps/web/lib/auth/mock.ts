'use client';

import type { AuthAdapter, AuthSession, AuthUser } from './adapter';
import { MOCK_SESSION_COOKIE } from './constants';

/**
 * Local-dev mock auth adapter.
 *
 * Used when NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are unset.
 * Keeps an in-memory session and mirrors it into the `lf_mock_session` cookie
 * so middleware and server components see the same session.
 * Synthetic data only — never a real account.
 */

const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

let memorySession: AuthSession | null = null;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function writeCookie(session: AuthSession | null): void {
  if (typeof document === 'undefined') return;
  if (session === null) {
    document.cookie = `${MOCK_SESSION_COOKIE}=; path=/; max-age=0; SameSite=Lax`;
    return;
  }
  const value = encodeURIComponent(JSON.stringify(session));
  document.cookie = `${MOCK_SESSION_COOKIE}=${value}; path=/; max-age=${SESSION_TTL_MS / 1000}; SameSite=Lax`;
}

function readCookie(): AuthSession | null {
  if (typeof document === 'undefined') return memorySession;
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${MOCK_SESSION_COOKIE}=([^;]*)`));
  if (!match) return null;
  try {
    return JSON.parse(decodeURIComponent(match[1])) as AuthSession;
  } catch {
    return null;
  }
}

function syntheticUser(provider: AuthUser['provider']): AuthUser {
  return {
    id: `mock-user-${Math.random().toString(36).slice(2, 10)}`,
    email: 'demo.user@example.invalid',
    displayName: 'Demo User (mock)',
    provider,
  };
}

export function createMockAdapter(): AuthAdapter {
  return {
    async signInWithGoogle(): Promise<void> {
      await delay(600);
      const session: AuthSession = {
        user: syntheticUser('mock'),
        expiresAt: new Date(Date.now() + SESSION_TTL_MS).toISOString(),
      };
      memorySession = session;
      writeCookie(session);
    },

    async signInWithMagicLink(email: string): Promise<void> {
      // Mock: pretend the link was sent; the caller shows the "sent" state.
      void email;
      await delay(600);
    },

    async signUpWithPassword(
      email: string,
      _password: string,
    ): Promise<{ needsConfirmation: boolean }> {
      void _password;
      await delay(600);
      const session: AuthSession = {
        user: { ...syntheticUser('mock'), email },
        expiresAt: new Date(Date.now() + SESSION_TTL_MS).toISOString(),
      };
      memorySession = session;
      writeCookie(session);
      // Mock mode never requires email confirmation.
      return { needsConfirmation: false };
    },

    async signInWithPassword(email: string, _password: string): Promise<void> {
      void _password;
      await delay(600);
      const session: AuthSession = {
        user: { ...syntheticUser('mock'), email },
        expiresAt: new Date(Date.now() + SESSION_TTL_MS).toISOString(),
      };
      memorySession = session;
      writeCookie(session);
    },

    async sendPasswordReset(email: string): Promise<void> {
      // Mock: pretend the reset email was sent; the caller shows the "sent" state.
      void email;
      await delay(600);
    },

    async updatePassword(_newPassword: string): Promise<void> {
      // Mock: nothing to persist — the synthetic session is unchanged.
      void _newPassword;
      await delay(400);
    },

    async getSession(): Promise<AuthSession | null> {
      const session = readCookie();
      if (!session) return null;
      if (new Date(session.expiresAt).getTime() < Date.now()) {
        memorySession = null;
        writeCookie(null);
        return null;
      }
      return session;
    },

    async signOut(): Promise<void> {
      await delay(200);
      memorySession = null;
      writeCookie(null);
    },

    async getAccessToken(): Promise<string | null> {
      // Mock sessions are synthetic — they carry no real JWT, so API calls
      // stay unauthenticated in mock mode (as before this integration).
      return null;
    },
  };
}
