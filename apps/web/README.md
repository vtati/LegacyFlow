# @legacyflow/web — LegacyFlow web app (Slice 1)

Next.js App Router front end for LegacyFlow. Slice 1 ships the foundation:
design-system wiring, auth (Supabase with a local-dev mock fallback), the
authenticated app shell with family/firm/admin navigation, and placeholder
pages. No real legal content anywhere — every screen is labeled
attorney-pending.

## Prerequisites

- Node 24+, pnpm 10+

## Install & run

```bash
pnpm install     # from the repo root, or from this directory
pnpm dev         # http://localhost:3000
```

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | only for real auth | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | only for real auth | Supabase anon key |

**Mock auth (default local dev):** when the two variables above are unset, the
app uses `lib/auth/mock.ts` — an in-memory adapter backed by the
`lf_mock_session` cookie. The login page shows a prominent banner:
“Local dev mock auth — no real Supabase configured.” Signing in with Google
creates a *synthetic* demo user (`demo.user@example.invalid`); the magic-link
form enters a mock “sent” state. Nothing leaves the browser. Set the env vars
to use real Supabase auth (Google OAuth via `signInWithOAuth`, magic links via
`signInWithOtp`, `/auth/callback` code exchange).

## Routes

- `/` — public landing
- `/about` — demo-only / attorney-pending explainer (§2.3 safety statements)
- `/login` — Google OAuth button + magic-link form
- `/family/*`, `/firm/*`, `/admin/*` — authenticated; middleware redirects
  anonymous users to `/login?next=…`. Each section renders its §5.5 nav and
  shared placeholder pages (“Coming in a later slice”) via one
  `PlaceholderPage` component — no per-page duplication.

## Checks

```bash
pnpm typecheck   # tsc --noEmit (strict)
pnpm lint        # eslint
pnpm test        # vitest run (jsdom component tests)
pnpm build       # next build
```

## Notes

- Design tokens come from `@legacyflow/design-tokens` (`--lf-*` CSS variables
  + Tailwind v3 preset). UI primitives are shadcn-style components owned
  locally under `components/ui/` (the shadcn CLI prompts interactively, so the
  set was hand-placed instead).
- Safety label (every footer + about page): “Demo scaffold — attorney-pending.
  Not legal advice. Synthetic data only.”
- Accessibility: skip-to-content link, labeled forms with inline errors,
  `aria-current` nav state, keyboard-focusable controls with visible focus rings.
