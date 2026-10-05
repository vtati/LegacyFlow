'use client';

import { createBrowserClient } from '@supabase/ssr';
import type { AuthAdapter, AuthSession } from './adapter';

function getBrowserSupabase() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      'Supabase is not configured: missing NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY.',
    );
  }
  return createBrowserClient(url, anonKey);
}

/** Real Supabase adapter (browser). Used when the env vars are configured. */
export function createSupabaseBrowserAdapter(): AuthAdapter {
  return {
    async signInWithGoogle(): Promise<void> {
      const supabase = getBrowserSupabase();
      const next =
        typeof window !== 'undefined'
          ? (new URLSearchParams(window.location.search).get('next') ?? '/family/home')
          : '/family/home';
      const { error } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: {
          redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
        },
      });
      if (error) throw error;
    },

    async signInWithMagicLink(email: string): Promise<void> {
      const supabase = getBrowserSupabase();
      const { error } = await supabase.auth.signInWithOtp({
        email,
        options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
      });
      if (error) throw error;
    },

    async getSession(): Promise<AuthSession | null> {
      const supabase = getBrowserSupabase();
      const {
        data: { session },
        error,
      } = await supabase.auth.getSession();
      if (error) throw error;
      if (!session?.user) return null;
      // Supabase reports 'google' for OAuth and 'email' for password/magic-link
      // sign-ins via app_metadata.provider — surface the real one.
      const rawProvider = session.user.app_metadata?.['provider'];
      return {
        user: {
          id: session.user.id,
          email: session.user.email ?? null,
          displayName: session.user.user_metadata?.['full_name'] ?? session.user.email ?? null,
          provider: rawProvider === 'google' ? 'google' : 'email',
        },
        expiresAt: new Date((session.expires_at ?? 0) * 1000).toISOString(),
      };
    },

    async signOut(): Promise<void> {
      const supabase = getBrowserSupabase();
      const { error } = await supabase.auth.signOut();
      if (error) throw error;
    },

    async getAccessToken(): Promise<string | null> {
      const supabase = getBrowserSupabase();
      const {
        data: { session },
        error,
      } = await supabase.auth.getSession();
      if (error) throw error;
      return session?.access_token ?? null;
    },

    async signUpWithPassword(
      email: string,
      password: string,
    ): Promise<{ needsConfirmation: boolean }> {
      const supabase = getBrowserSupabase();
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback?next=/family/home`,
        },
      });
      if (error) throw error;
      // With "confirm email" enabled Supabase creates the user but returns no
      // session until the confirmation link is followed.
      return { needsConfirmation: !data.session };
    },

    async signInWithPassword(email: string, password: string): Promise<void> {
      const supabase = getBrowserSupabase();
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) throw error;
    },

    async sendPasswordReset(email: string): Promise<void> {
      const supabase = getBrowserSupabase();
      const { error } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth/callback?next=/auth/update-password`,
      });
      if (error) throw error;
    },

    async updatePassword(newPassword: string): Promise<void> {
      const supabase = getBrowserSupabase();
      const { error } = await supabase.auth.updateUser({ password: newPassword });
      if (error) throw error;
    },
  };
}
