'use client';

import type { AuthAdapter } from './adapter';
import { isMockAuth } from './adapter';
import { createMockAdapter } from './mock';
import { createSupabaseBrowserAdapter } from './supabase-browser';

let cached: AuthAdapter | null = null;

/** Pick the right adapter for this environment (client-side). */
export function getAuthAdapter(): AuthAdapter {
  if (!cached) {
    cached = isMockAuth() ? createMockAdapter() : createSupabaseBrowserAdapter();
  }
  return cached;
}

export { isMockAuth };
