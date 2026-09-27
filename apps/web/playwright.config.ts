import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E for @legacyflow/web (PRD §12 critical path, §5.9 WCAG 2.2 AA).
 *
 * Test strategy:
 * - Chromium only — the CI-supported target; keeps the matrix small.
 * - The webServer runs `pnpm dev` on port 3100 rather than `next start` on a
 *   production build. Reason: the mock auth adapter is selected by the
 *   NEXT_PUBLIC_SUPABASE_* env vars, which Next.js inlines into a production
 *   build at *build* time. Pinning the mock env on the dev server is
 *   deterministic in every environment (process.env takes precedence over
 *   .env.local in Next.js), whereas `next start` would silently pick up real
 *   credentials if the build had ever been made with a dev .env.local present.
 * - Mock auth is forced via the webServer env below so the suite runs
 *   identically in CI (no .env.local) and on dev machines (a .env.local with
 *   real Supabase keys must not leak into E2E).
 * - The FastAPI API is NOT required: API responses are stubbed per-test at the
 *   shell level with synthetic fixtures; API reachability is covered by a
 *   dedicated graceful-skip test (see e2e/critical-path.spec.ts).
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // Task directive: no retries in CI — failures surface immediately.
  retries: 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: 'list',
  timeout: 90_000,
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL: 'http://localhost:3100',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          // Sandbox/CI without the Playwright browser download (e.g. the
          // browser CDN is unreachable): point at a system Chromium instead —
          //   PLAYWRIGHT_CHROMIUM_PATH=/opt/meta-chromium/chrome pnpm e2e
          // CI installs the pinned browser via `playwright install chromium`
          // and leaves this env var unset.
          executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH,
          args: ['--no-sandbox'],
        },
      },
    },
  ],
  webServer: {
    command: 'pnpm dev --port 3100',
    url: 'http://localhost:3100',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: {
      // Empty values are falsy, so lib/auth/adapter.ts `isMockAuth()` returns
      // true and the local mock adapter (synthetic demo user) is used.
      NEXT_PUBLIC_SUPABASE_URL: '',
      NEXT_PUBLIC_SUPABASE_ANON_KEY: '',
    },
  },
});
