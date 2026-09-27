import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Slice 1: demo scaffold. Transpile workspace packages so the Tailwind
  // preset and shared types resolve from source in dev.
  transpilePackages: ['@legacyflow/design-tokens', '@legacyflow/contracts', '@legacyflow/ui'],
  async rewrites() {
    // Staging/prod: the browser client calls the API same-origin (/v1/*).
    // When API_INTERNAL_URL is set (e.g. on Vercel), proxy those calls to
    // the FastAPI backend so no CORS or client changes are needed.
    // Unset locally — dev still targets same-origin.
    const apiBase = process.env.API_INTERNAL_URL;
    if (!apiBase) return [];
    const base = apiBase.replace(/\/$/, '');
    return [{ source: '/v1/:path*', destination: `${base}/v1/:path*` }];
  },
};

export default nextConfig;
