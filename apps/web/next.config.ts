import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Slice 1: demo scaffold. Transpile workspace packages so the Tailwind
  // preset and shared types resolve from source in dev.
  transpilePackages: ['@legacyflow/design-tokens', '@legacyflow/contracts', '@legacyflow/ui'],
};

export default nextConfig;
