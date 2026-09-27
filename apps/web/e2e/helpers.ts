import type { Page } from '@playwright/test';

/**
 * Navigation helper for the E2E suite.
 *
 * Why `gotoApp()` exists instead of `page.goto()`: some Chromium builds
 * (observed on the system Chromium 152 used when the Playwright browser CDN
 * is unreachable) enforce Local Network Access checks on *programmatic*
 * navigations (CDP `Page.navigate`, i.e. `page.goto()`). A fresh tab is
 * `about:blank` with an opaque origin, which the LNA check classifies as a
 * *public* initiator — so `page.goto('http://localhost:3100/…')` fails with
 * `net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS`, even though the user
 * typed the same URL into the omnibox would load fine.
 *
 * What *is* allowed:
 * - a renderer-initiated navigation (link click, `window.location.href = …`)
 *   from a `file://` page to a loopback URL, and
 * - a renderer-initiated navigation from a loopback page to another
 *   loopback URL (loopback → loopback is not a "local network request").
 *
 * So the first navigation of each test goes through `e2e/trampoline.html`
 * (a `file://` page that immediately redirects to the app URL), and later
 * navigations use `window.location.href` from the already-loaded app page.
 * On browsers without the aggressive LNA behavior this is just a slightly
 * slower — but equally correct — way to navigate, so the helper is safe to
 * use everywhere, including CI with the official Playwright Chromium.
 */

/** Must match `use.baseURL` in playwright.config.ts. */
export const APP_BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:3100';

/** `file://` URL of the trampoline page next to this module. */
const TRAMPOLINE_URL = new URL('./trampoline.html', import.meta.url).href;

const LOOPBACK_RE = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?\//i;

function resolveAppUrl(path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  return `${APP_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

/**
 * Navigate the page to an app URL (relative path or absolute loopback URL).
 */
export async function gotoApp(page: Page, path: string): Promise<void> {
  const url = resolveAppUrl(path);
  if (!LOOPBACK_RE.test(url)) {
    throw new Error(`gotoApp() only supports loopback app URLs, got: ${url}`);
  }

  if (LOOPBACK_RE.test(page.url())) {
    // Already on a loopback page: a renderer-initiated navigation to another
    // loopback URL is permitted by the LNA check.
    await Promise.all([
      page.waitForURL((u) => u.href === url, { timeout: 30_000 }),
      page.evaluate((target: string) => {
        window.location.href = target;
      }, url),
    ]);
  } else {
    // Fresh tab (about:blank): CDP navigation would be treated as coming
    // from a public initiator and blocked — trampoline through the file://
    // page instead.
    await page.goto(`${TRAMPOLINE_URL}?target=${encodeURIComponent(url)}`);
    await page.waitForURL((u) => u.href === url, { timeout: 30_000 });
  }
}
