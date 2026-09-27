/**
 * Auth adapter interface — one surface for both the real Supabase adapter
 * and the local-dev mock adapter.
 */

export interface AuthUser {
  id: string;
  email: string | null;
  displayName: string | null;
  provider: 'google' | 'magic-link' | 'mock';
}

export interface AuthSession {
  user: AuthUser;
  expiresAt: string;
}

export interface AuthAdapter {
  /** Start Google OAuth (or mock sign-in as a synthetic user). */
  signInWithGoogle(): Promise<void>;
  /** Send a magic link (or enter the mock "sent" state). */
  signInWithMagicLink(email: string): Promise<void>;
  /**
   * Create an account with an email and password.
   * Resolves with `needsConfirmation: true` when the user must confirm via
   * email before the first sign-in (Supabase "confirm email" setting).
   */
  signUpWithPassword(email: string, password: string): Promise<{ needsConfirmation: boolean }>;
  /** Sign in with an email and password. */
  signInWithPassword(email: string, password: string): Promise<void>;
  /** Send a password-reset email (or mock the "sent" state). */
  sendPasswordReset(email: string): Promise<void>;
  /**
   * Set a new password for the current user. Used after the user follows a
   * password-recovery link (recovery session) and from account settings.
   */
  updatePassword(newPassword: string): Promise<void>;
  /** Current session, or null when signed out. */
  getSession(): Promise<AuthSession | null>;
  /**
   * JWT to send as the API `Authorization: Bearer` token, or null when the
   * session carries none. Real adapter: the Supabase access token. Mock
   * adapter: always null (mock sessions are not real JWTs).
   */
  getAccessToken(): Promise<string | null>;
  signOut(): Promise<void>;
}

/**
 * Mock mode is active when the Supabase env vars are unset — the standard
 * local-dev path for Slice 1. The login UI must label this clearly.
 */
export function isMockAuth(): boolean {
  return !process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
}
