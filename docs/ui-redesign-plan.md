# LegacyFlow UI/UX Makeover — Redesign Plan

**Status:** plan only (no code changed)
**Date:** 2026-10-02
**Scope:** `apps/web` (Next.js) + `packages/design-tokens` + `packages/ui`
**Tone mandate:** estate planning = trust, legacy, family. Warm, calm, editorial. Never gimmicky.

---

## 0. Research references

Real sources studied for this plan (all verified live during research):

**Visual directions**
- Claude/Anthropic design-system teardown — warm cream canvas `#faf9f5`, terracotta `#c96442`, slab-serif display + Inter body, thin warm borders, near-zero shadow. The closest existing product to the tone LegacyFlow needs.
  https://github.com/hangodek/creative-agent/blob/HEAD/skills/design-systems/design-claude/SKILL.md
- wealth.com brand guidelines — modern serif (Reckless) for headlines + practical sans (Neue Haas Unica → Inter) for UI. The private-wealth type pattern: serif = heritage, sans = clarity.
  https://attachments.are.na/21257659/05285a40d0975397df583b9e94c1b07c.pdf?1680719277
- FinTrack design system — "calm, precise, intelligent, credible, quietly premium, human"; *information first, decoration second*; identity must survive with all decoration removed.
  https://github.com/dvynshuu/fintrack/blob/HEAD/.agents/skills/fintrack-design-system/SKILL.md
- Dribbble "world-class design" mode guide — LegacyFlow app = **Corporate/Enterprise** mode (trust through precision, typography rhythm, data clarity, measured motion, zero gimmicks); landing page = **Expressive** mode (one signature element, one justified aesthetic risk).
  https://github.com/hesab59hesba/important-files/blob/HEAD/dribble-world-class-design/SKILL.md
- 21st.dev component taxonomy (ai-chats, cards, tables, badges, spinner-loaders, forms) + shadcn/ui conventions — used as the component vocabulary, not a dependency.
  https://github.com/trin-zenityx/21st-dev-builder-v1/blob/HEAD/SKILL.md
- Luxury private-estate web design (Dribbble) — deep black, warm gold, cinematic restraint; confirms the "quiet authority" register, mined for the landing page only.
  http://dribbble.com/shots/27309667-B2C-Luxury-Real-Estate-Private-Estate-Management-Web-Design
- Finance/banking trust patterns roundup — what makes financial UIs feel safe: plain-language explanations, visible security cues, structured data display.
  https://reallygooddesigns.com/finance-banking-website-design/

**What "premium" actually means here (distilled):** palette discipline (3 hues max + warm neutrals), one serif used sparingly for voice, generous whitespace as a design element, thin warm borders instead of shadows, micro-interactions measured in milliseconds, empty states that guide instead of decorate, data displayed with tabular numerals and quiet hierarchy.

---

## 1. Recommended visual direction

### 1A. RECOMMENDED — "Heritage Editorial"

Warm, literary, quietly premium. Think a private client's leather-bound folder, not a SaaS dashboard. It evolves the current warm canvas rather than replacing it, and it differentiates LegacyFlow from every indigo dashboard on the internet.

**Palette (light only — no dark mode in this pass)**

| Token role | Value | Usage |
|---|---|---|
| Canvas | `#FAF7F1` | page background (warm ivory) |
| Canvas subtle | `#F3EEE3` | wells, hover washes |
| Elevated | `#FFFFFF` | cards, chat surfaces |
| Ink (text) | `#1C1917` | primary text, primary buttons |
| Text muted | `#57534E` | secondary text |
| Text subtle | `#78716C` | metadata, timestamps |
| Border | `#E8E2D5` | hairlines (warm, not blue-gray) |
| Border strong | `#D9D1BE` | inputs, dividers that must read |
| Primary scale | warm charcoal ramp (50 `#F5F2EA` → 900 `#1C1917` → 950 `#12100D`) | primary CTAs = near-black rectangles, ivory text (Claude pattern) |
| Accent (terracotta) | 500 `#C15F3C`, 600 `#A94F31`, 700 `#8A3E26`, tint `#F7E3D7` | ONE signature accent: "Start interview", active nav pill, progress fill, key highlights |
| Success (sage) | 600 `#4D7C6B`, 700 `#3D6659`, tint `#E4EFE9` | confirmations, complete states |
| Error | 600 `#C2410C`-family warm red, tint `#FBEAE4` | errors, destructive (never color-only: always paired with icon + text) |
| Gold (sparingly) | `#A98A3D` | landing-page flourishes + "plan complete" seal moments ONLY, never in app chrome |

Contrast notes: terracotta 600 `#A94F31` on white ≈ 5.0:1 (AA for body text); terracotta 500 reserved for large text/icons/tints. Ink `#1C1917` on ivory ≈ 15:1.

**Typography**
- Display serif: **Fraunces** (Google Fonts, 400/500/600, optical sizing) — landing hero, page titles (`h1`), empty-state headings, interview welcome, document seals. Tight letter-spacing (`-0.02em`), line-height 1.1–1.25.
- Body/UI: **Inter** (400/500/600) — everything else. 15–16px body, relaxed line-height 1.6 for prose/chat.
- Mono: system mono (unchanged) — ids, tokens, timestamps.
- Load via `next/font/google` in `app/layout.tsx` (currently **no** font is actually loaded — the stack names Geist but it silently falls back; this is an audit finding, §2).
- Rules: sentence case everywhere; one `h1` per page; serif never inside dense controls, tables, or chat bubbles.

**Spacing / radius / shadow**
- 4px grid; section rhythm 24 / 32 / 48 / 64; card padding 24 (`p-6`); page gutters 16 mobile / 24 tablet / 32 desktop.
- Radius: cards 14px, controls 8–10px, pills only for badges/toggles/status.
- Elevation: thin warm borders do the work; **one** soft warm shadow reserved for popovers/modals/composer (`0 8px 24px rgb(28 25 23 / 0.08)`). No card drop-shadows.

**Motion**
- 120–180ms ease-out color/opacity transitions; message entry = 160ms fade + 4px rise; typing indicator = three-dot pulse. All disabled under `prefers-reduced-motion` (already handled in `globals.css` — keep).

### 1B. Runner-up — "Private Bank"
Deep navy `#101E3C` + brass `#B08D3E` + ivory. Classic wealth-management register; maximal trust signaling. Rejected: reads cold and institutional for a *family* product, brass is hard to keep AA-accessible, and navy+gold is the most imitated "premium finance" cliché in the Dribbble set.

### 1C. Runner-up — "Modern Trust" (lowest-risk evolution)
Keep the current indigo/navy primary, deepen it, warm the neutrals, add Fraunces for display. Rejected as the headline direction because indigo is the default SaaS accent — it cannot make LegacyFlow feel distinctive — but it remains the fallback if stakeholder review balks at terracotta.

---

## 2. Audit findings (what's wrong today)

1. **Dead token classes:** `interview-chat.tsx` uses `bg-lf-danger-50`, `border-lf-warning-200`, `text-lf-danger-700`, etc. — `lf-danger`/`lf-warning` **do not exist** in `tokens.css`. Those error/warning states render unstyled. Fix by mapping to the `lf-error` scale + a new `lf-warning` scale (or reuse accent tint).
2. **Fonts never load:** `--lf-font-sans: Geist, Inter, …` but no `next/font` call anywhere — silently falls back to system. Same for the Georgia serif (weak for display use).
3. **Generic indigo primary** (`#4f46e5`/`#1e3a8a`): reads as "another SaaS dashboard", not "my family's legacy".
4. **Chat is the hero flow and looks the most dated:** both sides in 8px bubbles at 85% width, indigo user bubble, bare `<details>` for Sources, "Thinking…" as a static bubble, utilitarian composer.
5. **Landing page is structurally sound but flat:** single column, one card row, no visual signature.
6. **Two button implementations** (`packages/ui/src/button.tsx` and `apps/web/components/ui/button.tsx`) — must be kept in sync or unified.
7. **App shell is solid** (sticky header, sidebar, mobile nav, skip link, save indicator) — refine, don't rebuild.
8. **No dark mode** — confirmed absent; deliberately out of scope (§5).

---

## 3. Component-by-component upgrade list

All changes are token + class-level. **No behavior, prop, or API changes.**

| Component | File(s) | Change |
|---|---|---|
| Design tokens | `packages/design-tokens/src/tokens.css`, `src/tokens.ts`, `src/__tests__/tokens.test.ts` | Re-value the palette per §1A (keep token *names/shapes* — `lf-primary-50…950`, radius, shadow keys — so all consuming classes keep working). Add `lf-warning` scale (warm amber 50–700). Update font stacks to the loaded Fraunces/Inter. Update the token tests to the new values. |
| Button | `packages/ui/src/button.tsx`, `apps/web/components/ui/button.tsx` | `primary` → ink `#1C1917` bg, ivory text, hover `#2A2622`; `accent` → terracotta 600 bg, white text, hover 700; `outline`/`ghost` → warm borders/washes; `destructive` → warm red 600. Keep sizes, focus rings (recolor to terracotta-500), disabled states. Unify the two implementations' variants. |
| Card | `packages/ui/src/card.tsx`, `apps/web/components/ui/card.tsx` | Warm 1px border, radius 14px, **remove** `shadow-lf-card` from default (reserve shadow for overlay contexts). `CardTitle` → serif optional via `font-lf-serif` where editorial (page headers, not data cards). |
| Chat messages | `apps/web/components/family/interview/interview-chat.tsx` (~835 lines — the flagship) | Assistant: **no bubble** — open editorial prose on canvas with a small ink monogram avatar; max-w ~68ch. User: warm-tinted bubble (`bg-lf-primary-50` warm) right-aligned, 10px radius with 2px bottom-right corner cut. Citations: replace bare `<details>` with a refined "Sources (n)" footnote expander — numbered markers, serif "Sources" label, warm hairline separators. Typing: three-dot pulse indicator (aria-live). Timestamps on hover. |
| Composer | same file | Elevated rounded-2xl bar (`bg-white`, warm border, soft shadow), auto-growing textarea, icon buttons (mic, attach) left, ink send button right; focus ring terracotta; disabled/denied states keep today's graceful copy. |
| Proposal cards | same file | Structured card: field name (serif?), current → proposed diff rows, Confirm / Edit / Reject as three clear actions (ink / outline / destructive-ghost). Keep all current logic and copy. |
| Consistency review | same file | Findings list with sage/warm-amber severity accents, "Re-run" affordance unchanged. |
| Inputs / labels | `apps/web/components/ui/input.tsx`, `label.tsx` | 44px min-height, warm borders, terracotta focus ring, generous padding; error state = warm-red border + icon + text (never color-only). |
| Badges / status pills | `apps/web/lib/format.ts` (`statusBadgeClass`) | Warm-neutral pills with tinted dot: sage = complete, terracotta tint = in-progress, warm gray = not started, red tint = attention. |
| Empty states | `apps/web/components/family/page-state.tsx` | Serif headline (Fraunces 500), lucide icon in warm tinted circle, one clear CTA; keep `LoadingState` skeletons (recolor to warm) and `ErrorState` copy/logic. |
| Progress | `apps/web/components/family/progress-bar.tsx` | Terracotta fill on warm track, 6px, rounded; keep semantics. |
| Nav / shell | `apps/web/components/app-shell.tsx`, `section-nav.tsx`, `mobile-nav.tsx`, `account-menu.tsx`, `save-indicator.tsx` | Sidebar: section label (uppercase, warm subtle), items with 8px radius, active = warm tint wash + ink text + terracotta 2px left indicator; header: wordmark in Fraunces 600; keep sticky header, mobile hamburger, save indicator logic. |
| Modal | `apps/web/components/ui/modal.tsx` | Warm scrim (`rgb(28 25 23 / 0.4)`), 16px radius, soft warm shadow, 200ms fade/scale-in. |
| Document list | `app/(app)/family/documents/page.tsx`, `components/documents/*` | Document rows as refined list-cards: serif title, warm metadata, ink download button; keep watermark notice copy and behavior. |
| Tables (firm/admin) | `app/(app)/firm/**`, `app/(app)/admin/**` | Sticky headers, warm hairline rows, hover wash, tabular numerals; no structural changes. |

---

## 4. Page-by-page change list

| Route | Change |
|---|---|
| `/` landing | **Expressive redesign** (the one justified risk): warm ivory canvas, Fraunces hero ("Estate planning your family can actually finish"), terracotta primary CTA, dark ink band for the attorney-review pillar + pre-footer CTA, refined pillar cards with hairlines. Keep copy, safety label, footer. |
| `/about` | Same editorial treatment, lighter touch. |
| `/login`, `/signup`, `/auth/reset-password`, `/auth/update-password` | Split layout: left editorial panel (ink background, Fraunces headline, quiet trust copy) / right form card. Keep all auth logic (Google OAuth, magic link, password). |
| `/family/home` | Welcome header in Fraunces, matter cards with warm borders + status pills, quick actions as refined tiles. "No matters yet" → the new empty-state pattern. |
| `/family/interview/[matterId]` | The flagship — §3 chat/composer/proposal upgrades. |
| `/family/intake`, `/family/intake/[matterId]` | Guided intake: sectioned cards, warm focus states, serif section titles. No question-logic changes. |
| `/family/documents`, `/family/files`, `/family/vault` | §3 document-list treatment; vault keeps its security posture copy. |
| `/family/assets`, `/family/assets-accounts`, `/family/liabilities`, `/family/dependents`, `/family/guardians`, `/family/family-guardians`, `/family/funding`, `/family/checkup`, `/family/my-plan`, `/family/signing`, `/family/profile`, `/family/help` | Apply component upgrades; serif page titles; consistent section spacing. No form-logic changes. |
| `/firm/**` (overview, matters, clients, review-queue, reports, knowledge-rules, templates, signing, funding, team-settings) | Table/queue polish per §3; review queue keeps its exact workflow. |
| `/admin/**` (system-health, content-packs, feature-flags, firms, jurisdictions, security-audit, support, templates) | Same polish; system-health keeps its live checks verbatim. |
| `/vault/[token]` | Keep flow identical; warm visual treatment only. |

---

## 5. Responsive strategy

- **Breakpoints:** Tailwind defaults (`sm:640`, `md:768`, `lg:1024`, `xl:1280`). Sidebar appears at `md:` (already); page gutters 16 → 24 → 32.
- **Chat (mobile):** full-height column, sticky composer above the keyboard, messages max-w-full with 16px gutters, proposal cards stack actions vertically, touch targets ≥44px.
- **Tables (firm/admin):** below `md:`, rows become stacked cards (label/value pairs) — CSS-only via existing responsive utilities; no new table component.
- **Landing:** hero type scales 40px → 56px → 64px; pillar grid 1 → 3 cols; dark band stacks.
- **Auth split:** stacks to single column below `lg:` (form first, editorial panel as compact header).
- **Verification:** screenshot every primary route at **390×844** (mobile), **768×1024** (tablet), **1440×900** (desktop) — same three viewports the E2E recording already covers.

---

## 6. Non-goals (do NOT touch)

- **No behavior changes.** Every interactive flow keeps working *exactly* as today: AI interview turns, proposal confirm/edit/reject, consistency review + idempotent re-run, dictation (incl. graceful browser-denial path), document upload/extraction (PDF/DOCX/OCR notice/injection handling), guided intake question logic, pause/resume, session completion, matter switching, auth (Google OAuth / magic link / password reset), invitation accept, cross-tenant blocking.
- **No API changes** (`services/api`) and no contract changes (`@legacyflow/contracts`).
- **No new runtime dependencies.** `next/font/google` is built into Next.js. No shadcn CLI install, no 21st.dev codegen, no animation libraries — CSS transitions only.
- **No dark mode** in this pass (token architecture supports adding it later via a `[data-theme]` override).
- **No copy rewrites** beyond what styling requires — especially legal/safety copy, error strings, and the "AI intake assistant / not an attorney" language.
- **Risky to touch:** `interview-chat.tsx` message-state machine (edit only JSX/classes, never hooks/logic), `api-client.ts`, auth adapter, `middleware.ts`, encryption/PDF pipelines.

---

## 7. Build / verify checklist

1. `tsc --noEmit` in `apps/web` — must pass.
2. **ESLint** on every changed/new web file (`./node_modules/.bin/eslint`) — per the standing lesson, Vercel's build runs ESLint and flags unused vars that `tsc` ignores.
3. `vitest` in `packages/design-tokens` (token tests) and `packages/ui`.
4. Existing web unit tests (`vitest.config.ts` in `apps/web`) — must stay green.
5. Viewport screenshots at 390 / 768 / 1440 for: `/`, `/login`, `/family/home`, `/family/interview/[matterId]`, `/family/documents`, `/family/intake`, one firm page, one admin page — eyeball for overflow, overlap, and touch-target size.
6. Contrast spot-checks: terracotta-600/white, ink/ivory, muted text on canvas (AA ≥ 4.5:1 for body).
7. `prefers-reduced-motion` — verify animations collapse (already in `globals.css`; re-verify after changes).
8. Full staging E2E (API suite + the 31-step UI walkthrough) on `legacyflow.genalphalabs.com` **before** the final narrated recording.
9. Recording gate: the parent agent re-records only when all gates pass — this plan changes presentation, so the E2E must re-run end to end after the makeover.

---

## Appendix — file map for implementers

- Tokens: `packages/design-tokens/src/tokens.css`, `src/tokens.ts`, `src/tailwind-preset.ts` (no shape changes needed), `src/__tests__/tokens.test.ts`
- Primitives: `packages/ui/src/{button,card}.tsx`, `apps/web/components/ui/{button,card,input,label,modal}.tsx`
- Shell: `apps/web/components/{app-shell,section-nav,mobile-nav,account-menu,save-indicator,footer}.tsx`
- Chat flagship: `apps/web/components/family/interview/interview-chat.tsx`
- States: `apps/web/components/family/page-state.tsx`, `progress-bar.tsx`
- Routes: `apps/web/app/**` per §4
- Fonts: `apps/web/app/layout.tsx` (`next/font/google`: Fraunces + Inter)
