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
          ? (new URLSearchParams(window.location.search).get('next') ?? '/')
          : '/';
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
      return {
        user: {
          id: session.user.id,
          email: session.user.email ?? null,
          displayName: session.user.user_metadata?.['full_name'] ?? session.user.email ?? null,
          provider: 'google',
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
  };
}
