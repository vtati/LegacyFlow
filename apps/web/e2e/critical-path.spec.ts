import { test, expect, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { gotoApp } from './helpers';

/**
 * Critical-path E2E for @legacyflow/web (PRD §12).
 *
 * Runs against the local mock auth adapter (forced by the webServer env in
 * playwright.config.ts — synthetic demo user only, never a real account).
 * API responses are stubbed per test with synthetic fixtures: these tests
 * cover the web shell (routing, auth gating, layout, rendering, a11y), not
 * the API. API reachability itself is checked by the graceful-skip
 * `/healthz` test at the bottom.
 */

const MATTER_ID = 'matter-e2e-001';
const HOUSEHOLD_ID = 'household-e2e-001';
const MOCK_SESSION_COOKIE = 'lf_mock_session';

const MATTER_ONE = {
  id: MATTER_ID,
  title: 'Oak Demo Household Estate Plan',
  jurisdiction_code: 'US-VA',
  status: 'intake',
  completeness_pct: 42,
  household: { id: HOUSEHOLD_ID, name: 'Oak Demo Household' },
  created_at: '2026-01-10T00:00:00.000Z',
  updated_at: '2026-09-20T00:00:00.000Z',
};

const MATTER_TWO = {
  id: 'matter-e2e-002',
  title: 'Rivera Demo Household Trust Review',
  jurisdiction_code: 'US-VA',
  status: 'attorney_review',
  completeness_pct: 87,
  household: { id: 'household-e2e-002', name: 'Rivera Demo Household' },
  created_at: '2026-02-14T00:00:00.000Z',
  updated_at: '2026-09-25T00:00:00.000Z',
};

const QUESTION_SETS = {
  jurisdiction_code: 'US-VA',
  pack_version: 'synthetic-v1',
  question_sets: {
    household: {
      question_set: { id: 'qs-household', title: 'Household' },
      sections: [
        {
          id: 'sec-household-basics',
          title: 'Household basics',
          questions: [
            {
              key: 'household.name',
              type: 'text',
              label: 'Household name',
              help: null,
              required: true,
            },
            {
              key: 'household.size',
              type: 'single_select',
              label: 'Household size',
              help: null,
              required: true,
              options: [
                { value: '1', label: 'Just me' },
                { value: '2plus', label: '2 or more' },
              ],
            },
          ],
        },
      ],
    },
    guardians: {
      question_set: { id: 'qs-guardians', title: 'Guardians' },
      sections: [
        {
          id: 'sec-guardian-nominations',
          title: 'Guardian nominations',
          questions: [
            {
              key: 'guardians.notes',
              type: 'notes',
              label: 'Guardian notes',
              help: null,
              required: false,
            },
          ],
        },
      ],
    },
    assets: {
      question_set: { id: 'qs-assets', title: 'Assets' },
      sections: [
        {
          id: 'sec-asset-overview',
          title: 'Asset overview',
          questions: [
            {
              key: 'assets.has_real_estate',
              type: 'boolean',
              label: 'Do you own real estate?',
              help: null,
              required: true,
            },
          ],
        },
      ],
    },
  },
};

const INTAKE_PROGRESS = {
  sets: {
    'qs-household': {
      total_questions: 2,
      answered: 1,
      required_total: 2,
      required_answered: 1,
      percent_complete: 50,
    },
    'qs-guardians': {
      total_questions: 1,
      answered: 0,
      required_total: 0,
      required_answered: 0,
      percent_complete: 0,
    },
    'qs-assets': {
      total_questions: 1,
      answered: 0,
      required_total: 1,
      required_answered: 0,
      percent_complete: 0,
    },
  },
  overall: {
    total_questions: 4,
    answered: 1,
    required_total: 3,
    required_answered: 1,
    percent_complete: 33,
  },
};

/** Sign the browser in as the synthetic mock user (mirrors lib/auth/mock.ts). */
async function signInAsMockUser(page: Page): Promise<void> {
  const session = {
    user: {
      id: 'mock-user-e2e-001',
      email: 'demo.user@example.invalid',
      displayName: 'Demo User (mock)',
      provider: 'mock',
    },
    expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  };
  await page.context().addCookies([
    {
      name: MOCK_SESSION_COOKIE,
      value: encodeURIComponent(JSON.stringify(session)),
      url: 'http://localhost:3100',
    },
  ]);
}

/** Stub the family/firm API surface with synthetic fixtures (shell-level E2E). */
async function stubApi(page: Page): Promise<void> {
  await page.route('**/v1/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const path = url.pathname.replace(/^\/v1/, '');
    const json = (body: unknown, status = 200): Promise<void> =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      });

    if (method === 'GET' && path === '/matters') {
      return json({ items: [MATTER_ONE, MATTER_TWO], page: 1, page_size: 20, total: 2 });
    }
    if (method === 'GET' && path === `/matters/${MATTER_ID}`) {
      return json({
        matter: MATTER_ONE,
        household: MATTER_ONE.household,
        progress: { completeness_pct: MATTER_ONE.completeness_pct, status: MATTER_ONE.status },
      });
    }
    if (method === 'GET' && path === `/matters/${MATTER_ID}/intake/question-sets`) {
      return json(QUESTION_SETS);
    }
    if (method === 'GET' && path === `/matters/${MATTER_ID}/intake/answers`) {
      return json({ answers: {}, updated_at: '2026-09-27T00:00:00.000Z' });
    }
    if (method === 'GET' && path === `/matters/${MATTER_ID}/household`) {
      return json({
        household: MATTER_ONE.household,
        members: [],
        persons: [],
        relationships: [],
      });
    }
    if (method === 'GET' && path === `/matters/${MATTER_ID}/intake/progress`) {
      return json(INTAKE_PROGRESS);
    }
    if (method === 'PUT' && path === `/matters/${MATTER_ID}/intake/answers`) {
      return json({ saved: true, progress: INTAKE_PROGRESS.overall });
    }
    return json({ message: 'Not stubbed in E2E' }, 404);
  });
}

/**
 * WCAG 2.2 AA automated check (PRD §5.9): fail the test when axe reports any
 * serious or critical violation. Minor/moderate findings are informational.
 */
async function expectNoSeriousOrCriticalViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  const blocking = results.violations.filter(
    (violation) => violation.impact === 'serious' || violation.impact === 'critical',
  );
  expect(
    blocking,
    `axe serious/critical violations: ${JSON.stringify(
      blocking.map((violation) => ({ id: violation.id, impact: violation.impact })),
      null,
      2,
    )}`,
  ).toEqual([]);
}

test.describe('critical path (mock auth)', () => {
  test('landing page loads with product messaging', async ({ page }) => {
    await gotoApp(page, '/');
    await expect(
      page.getByRole('heading', { name: 'Estate planning your family can actually finish' }),
    ).toBeVisible();
    await expect(
      page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Sign in' }),
    ).toBeVisible();
    await expect(page.locator('#main-content').getByRole('note')).toContainText('Demo scaffold');

    // Skip link exists and resolves to the main content landmark.
    const skipLink = page.getByRole('link', { name: 'Skip to content' });
    await skipLink.focus();
    await expect(skipLink).toBeVisible();
    await skipLink.click();
    await expect(page.locator('#main-content')).toBeVisible();

    await expectNoSeriousOrCriticalViolations(page);
  });

  test('login page renders Google and magic-link sign-in options', async ({ page }) => {
    await gotoApp(page, '/login');
    await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible();
    await expect(page.getByLabel('Email address')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Email me a sign-in link' })).toBeVisible();
    await expect(page.getByText('Local dev mock auth')).toBeVisible();

    await expectNoSeriousOrCriticalViolations(page);
  });

  test('mock Google sign-in redirects into the app shell', async ({ page }) => {
    await gotoApp(page, '/login');
    await page.getByRole('button', { name: 'Continue with Google' }).click();
    await expect(page).toHaveURL(/\/family\/home/);
    // Authenticated shell renders the account menu for the synthetic demo user.
    await expect(
      page.getByRole('button', { name: 'Account menu for Demo User (mock)' }),
    ).toBeVisible();
  });

  test('authenticated family dashboard loads with module cards', async ({ page }) => {
    await signInAsMockUser(page);
    await stubApi(page);
    await gotoApp(page, '/family');

    await expect(page.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeVisible();
    await expect(
      page.getByRole('heading', { name: 'Oak Demo Household Estate Plan' }),
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { name: 'Rivera Demo Household Trust Review' }),
    ).toBeVisible();
    // One continue action per matter card.
    await expect(page.getByRole('link', { name: /^Continue/ })).toHaveCount(2);

    await expectNoSeriousOrCriticalViolations(page);
  });

  test('intake wizard renders steps and progress', async ({ page }) => {
    await signInAsMockUser(page);
    await stubApi(page);
    await gotoApp(page, `/family/intake/${MATTER_ID}`);

    await expect(
      page.getByRole('heading', { name: `Intake: ${MATTER_ONE.title}`, level: 1 }),
    ).toBeVisible();
    const sectionsNav = page.getByRole('navigation', { name: 'Intake sections' });
    await expect(sectionsNav).toBeVisible();
    await expect(sectionsNav.getByRole('button')).toHaveCount(3);
    await expect(
      sectionsNav.getByRole('button', { name: /Household basics/ }),
    ).toHaveAttribute('aria-current', 'step');
    await expect(page.getByText('Overall progress')).toBeVisible();
    await expect(
      page.getByRole('progressbar', { name: 'Overall intake progress' }),
    ).toBeVisible();
    // First section's questions render.
    await expect(page.getByLabel('Household name')).toBeVisible();

    await expectNoSeriousOrCriticalViolations(page);
  });

  test('firm matters page renders', async ({ page }) => {
    await signInAsMockUser(page);
    await stubApi(page);
    await gotoApp(page, '/firm/matters');

    await expect(page.getByRole('heading', { name: 'Matters', level: 1 })).toBeVisible();
    await expect(page.getByRole('search', { name: 'Search matters' })).toBeVisible();
    const table = page.getByRole('table', { name: 'Client matters' });
    await expect(table).toBeVisible();
    await expect(table.getByRole('link', { name: MATTER_ONE.title })).toBeVisible();
    await expect(page.getByText('2 matters')).toBeVisible();
  });

  test('API /healthz reachable (skipped gracefully when the API is down)', async ({
    request,
  }) => {
    const apiUrl = process.env.API_URL ?? 'http://localhost:8000';
    const response = await request
      .get(`${apiUrl}/healthz`, { timeout: 5_000 })
      .catch((error: unknown) => {
        // The API is a separate service and is not required for the shell E2E:
        // log and skip instead of failing the suite.
        console.log(
          `[e2e] API not reachable at ${apiUrl}/healthz — skipping health check: ${String(error)}`,
        );
        test.skip(true, `API not running at ${apiUrl}`);
        throw error;
      });
    expect(response.ok(), `expected 2xx from ${apiUrl}/healthz, got ${response.status()}`).toBe(
      true,
    );
  });
});
