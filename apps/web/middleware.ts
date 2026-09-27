import { createServerClient, type CookieOptions } from '@supabase/ssr';
import { NextResponse, type NextRequest } from 'next/server';
import { MOCK_SESSION_COOKIE } from './lib/auth/constants';

function isMockAuth(): boolean {
  return !process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
}

function toLogin(request: NextRequest): NextResponse {
  const url = request.nextUrl.clone();
  url.pathname = '/login';
  url.searchParams.set('next', request.nextUrl.pathname);
  return NextResponse.redirect(url);
}

/**
 * Protects /family/*, /firm/* and /admin/*. Works with both auth adapters:
 * mock mode checks the lf_mock_session cookie; real mode validates the
 * Supabase session (and refreshes it).
 */
export async function middleware(request: NextRequest): Promise<NextResponse> {
  if (isMockAuth()) {
    const session = request.cookies.get(MOCK_SESSION_COOKIE)?.value;
    if (!session) return toLogin(request);
    return NextResponse.next();
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
