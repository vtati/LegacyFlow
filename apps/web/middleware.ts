import { createServerClient, type CookieOptions } from '@supabase/ssr';
import { NextResponse, type NextRequest } from 'next/server';
import { MOCK_SESSION_COOKIE } from './lib/auth/constants';
import { mockAuthDecision } from './lib/auth/mock-session';

function isMockAuth(): boolean {
  return !process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
}

function toLogin(request: NextRequest, error?: string): NextResponse {
  const url = request.nextUrl.clone();
  url.pathname = '/login';
  url.search = '';
  if (error) {
    url.searchParams.set('error', error);
  } else {
    url.searchParams.set('next', request.nextUrl.pathname);
  }
  return NextResponse.redirect(url);
}

/**
 * Protects /family/*, /firm/* and /admin/*. Works with both auth adapters:
 * mock mode checks the lf_mock_session cookie; real mode validates the
 * Supabase session (and refreshes it).
 */
export async function middleware(request: NextRequest): Promise<NextResponse> {
  // Defensive: if Supabase redirects an OAuth/magic-link `code` to any path
  // other than /auth/callback (e.g. the site root when the Supabase Site URL
  // or Redirect URLs allowlist doesn't match the requesting domain), forward
  // it to the callback handler so sign-in still completes.
  if (
    request.nextUrl.searchParams.has('code') &&
    request.nextUrl.pathname !== '/auth/callback'
  ) {
    const url = request.nextUrl.clone();
    url.pathname = '/auth/callback';
    return NextResponse.redirect(url);
  }

  if (isMockAuth()) {
    // SEC-001 (fail closed): mock mode is a local-dev convenience only.
    // In production an unset Supabase config is a misconfiguration, not
    // an invitation to trust a cookie — redirect to login with an error
    // flag instead of passing. Outside production, the cookie must
    // still parse as a well-formed, unexpired session (the same
    // validation getServerSession applies); mere presence is not proof.
    const decision = mockAuthDecision({
      nodeEnv: process.env.NODE_ENV,
      cookieValue: request.cookies.get(MOCK_SESSION_COOKIE)?.value,
    });
    if (decision === 'pass') return NextResponse.next();
    if (decision === 'misconfigured') return toLogin(request, 'auth_unavailable');
    return toLogin(request);
  }

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) return toLogin(request);

  let response = NextResponse.next({ request });
  const supabase = createServerClient(url, anonKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookiesToSet: { name: string; value: string; options: CookieOptions }[]) => {
        cookiesToSet.forEach(({ name, value, options }) => {
          request.cookies.set(name, value);
          response = NextResponse.next({ request });
          response.cookies.set(name, value, options);
        });
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return toLogin(request);
  return response;
}

export const config = {
  matcher: ['/family/:path*', '/firm/:path*', '/admin/:path*'],
};
