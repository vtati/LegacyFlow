import { redirect } from 'next/navigation';
import Link from 'next/link';
import { ArrowRight, Check, FileCheck2, ShieldCheck, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitleEditorial } from '@/components/ui/card';
import { Footer } from '@/components/footer';
import { ThemeToggle } from '@/components/theme-toggle';
import { LogoMark } from '@/components/logo-mark';
import { Reveal } from '@/components/reveal';
import { CountUp } from '@/components/count-up';
import { PRODUCT_NAME, SAFETY_LABEL } from '@/lib/constants';
import { getServerSession } from '@/lib/auth/server';

/** Public marketing page. Authenticated users go straight to their home. */
export default async function MarketingPage() {
  const session = await getServerSession();
  if (session) {
    redirect('/family/home');
  }
  return <MarketingContent />;
}

function MarketingContent() {
  return <LandingPage />;
}

const PILLARS = [
  {
    icon: Users,
    title: 'Built for families',
    body: 'A calm, guided way to organize the people, assets, and wishes that make up your plan.',
    tile: 'bg-lf-panel-sky text-lf-sky-700',
  },
  {
    icon: FileCheck2,
    title: 'Attorney-guided',
    body: 'Every plan is reviewed by a licensed attorney before anything is finalized. Nothing ships on autopilot.',
    tile: 'bg-lf-panel-violet text-lf-violet-700',
  },
  {
    icon: ShieldCheck,
    title: 'Private by design',
    body: 'Your information stays yours. Access is controlled, audited, and limited to your firm.',
    tile: 'bg-lf-panel-mint text-lf-success-700',
  },
] as const;

const ATTORNEY_PILLAR = PILLARS[1];

/** Public landing page — placeholder marketing copy, safety-labeled. */
function LandingPage(): React.JSX.Element {
  return (
    <div className="lf-aurora flex min-h-screen flex-col bg-lf-canvas">
      <header className="lf-glass sticky top-0 z-40 border-b border-lf-border">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-2.5">
            <LogoMark className="size-9" />
            <p className="font-lf-serif text-xl font-semibold tracking-tight text-lf-text">
              {PRODUCT_NAME}
            </p>
          </div>
          <nav className="flex items-center gap-2" aria-label="Primary">
            <ThemeToggle />
            <Button variant="ghost" asChild>
              <Link href="/about">About</Link>
            </Button>
            <Button asChild className="bg-lf-gradient-action text-white shadow-lf-glow hover:brightness-110">
              <Link href="/login">Sign in</Link>
            </Button>
          </nav>
        </div>
      </header>

      <main id="main-content" className="flex-1">
        <section className="relative overflow-hidden">
          <div className="lf-aurora lf-aurora-drift pointer-events-none absolute -inset-[12%]" aria-hidden="true" />
          <div className="relative mx-auto max-w-6xl px-4 py-16 text-center sm:px-6 sm:py-24 lg:px-8">
          <div className="mx-auto mb-8 h-1 w-16 rounded-full bg-lf-gradient" aria-hidden="true" />
          <h1 className="mx-auto max-w-4xl font-lf-serif text-[40px] font-semibold leading-[1.08] tracking-tight text-lf-text sm:text-[56px] lg:text-[64px]">
            Estate planning your family can{' '}
            <span className="bg-lf-gradient-action bg-clip-text text-transparent">actually finish</span>
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-lf-text-muted">
            Organize your household, guardians, and assets step by step — with a licensed attorney
            reviewing everything before it counts.
          </p>
          <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button size="lg" variant="accent" asChild className="bg-lf-gradient-action text-white shadow-lf-glow hover:brightness-110">
              <Link href="/login">
                Get started <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
            <Button size="lg" variant="outline" asChild className="bg-lf-canvas-elevated/70">
              <Link href="#how-it-works">How it works</Link>
            </Button>
          </div>
          <p className="mx-auto mt-8 max-w-xl text-xs leading-relaxed text-lf-text-subtle" role="note">
            {SAFETY_LABEL}
          </p>
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-4 sm:px-6 lg:px-8" aria-label="Product preview and key figures">
          <Reveal><ProductMock /></Reveal>
          <div className="grid gap-8 pb-16 pt-14 text-center sm:grid-cols-3 sm:pb-24">
            {[
              { end: 76, suffix: '%', fallback: '76%', label: 'of U.S. adults have no will', source: 'Caring.com 2025 Wills & Estate Planning Study' },
              { end: 84, prefix: '$', suffix: 'T', fallback: '$84T', label: 'in generational wealth transfers by 2045', source: 'Cerulli Associates' },
              { end: 100, suffix: '%', fallback: '100%', label: 'of plans reviewed by a licensed attorney', source: undefined },
            ].map((stat) => (
              <div key={stat.label}>
                <p className="font-lf-serif text-[40px] font-bold leading-none tracking-tight text-lf-text">
                  <CountUp end={stat.end} prefix={stat.prefix ?? ''} suffix={stat.suffix} fallback={stat.fallback} />
                </p>
                <p className="mx-auto mt-2 max-w-[240px] text-sm leading-relaxed text-lf-text-muted">{stat.label}</p>
                {stat.source ? (
                  <p className="mt-1 text-[11px] text-lf-text-subtle">Source: {stat.source}</p>
                ) : null}
              </div>
            ))}
          </div>
        </section>

        <section
          className="mx-auto max-w-6xl px-4 pb-16 sm:px-6 sm:pb-24 lg:px-8"
          aria-label="Why LegacyFlow"
        >
          <div className="grid gap-6 md:grid-cols-3">
            {PILLARS.map((pillar) => (
              <Card key={pillar.title} className="lf-lift bg-lf-canvas-elevated shadow-lf-card">
                <CardHeader>
                  <span className={`flex size-11 items-center justify-center rounded-lf-control ${pillar.tile}`}>
                    <pillar.icon className="size-5" aria-hidden="true" />
                  </span>
                  <CardTitleEditorial className="text-xl font-semibold">
                    {pillar.title}
                  </CardTitleEditorial>
                </CardHeader>
                <CardContent>
                  <p className="text-[15px] leading-relaxed text-lf-text-muted">{pillar.body}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>

        <section id="how-it-works" className="scroll-mt-20 bg-lf-panel-cyan" aria-label="How it works">
          <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8">
          <div className="mb-10 text-center">
            <h2 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text sm:text-4xl">
              How it works
            </h2>
            <p className="mx-auto mt-3 max-w-2xl text-lg text-lf-text-muted">
              Three steps from first question to attorney-ready documents.
            </p>
          </div>
          <div className="grid gap-6 md:grid-cols-3">
            {[
              {
                step: '1',
                title: 'Answer the interview',
                body: 'Our AI guide walks you through your household, guardians, assets, and wishes in plain language — no legal background needed.',
              },
              {
                step: '2',
                title: 'Review your drafts',
                body: 'Your answers become draft documents: will, healthcare directive, power of attorney, and trust. Review every value before submitting.',
              },
              {
                step: '3',
                title: 'Attorney reviews',
                body: 'A licensed attorney at your firm reviews everything, requests changes if needed, and releases your final documents.',
              },
            ].map((item) => (
              <Card key={item.step} className="bg-lf-canvas-elevated shadow-lf-card">
                <CardHeader>
                  <span className="flex size-11 items-center justify-center rounded-full bg-lf-gradient-action font-lf-serif text-lg font-semibold text-white shadow-lf-glow">
                    {item.step}
                  </span>
                  <CardTitleEditorial className="text-xl font-semibold">
                    {item.title}
                  </CardTitleEditorial>
                </CardHeader>
                <CardContent>
                  <p className="text-[15px] leading-relaxed text-lf-text-muted">{item.body}</p>
                </CardContent>
              </Card>
            ))}
          </div>
          </div>
        </section>

        <section
          className="mx-auto max-w-4xl px-4 py-16 sm:px-6 sm:py-24 lg:px-8"
          aria-label="Frequently asked questions"
        >
          <div className="mb-10 text-center">
            <h2 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text sm:text-4xl">
              Common questions
            </h2>
          </div>
          <Reveal><div className="space-y-4">
            {[
              {
                q: 'Is this legal advice?',
                a: 'No. LegacyFlow is an intake and document preparation tool. The AI helps you organize your information, but every plan is reviewed by a licensed attorney before anything is finalized. Nothing here constitutes legal advice.',
              },
              {
                q: 'Do I need to know legal terms to use this?',
                a: 'No. The interview is in plain language, and the AI explains any legal concept — like "testator" or "codicil" — when it comes up. If you\'re unsure about anything, you can ask.',
              },
              {
                q: 'Who sees my information?',
                a: 'Your information stays within your firm. Access is controlled, audited, and limited to your household and your firm\'s attorneys and staff.',
              },
              {
                q: 'What documents will I get?',
                a: 'Depending on your plan type: Last Will and Testament, Advance Medical Directive, Durable Power of Attorney, Revocable Living Trust, and related documents — all as attorney-reviewed drafts first.',
              },
            ].map((faq) => (
              <Card key={faq.q} className="bg-lf-canvas-elevated shadow-lf-card">
                <CardHeader className="pb-2">
                  <CardTitleEditorial className="text-lg font-semibold">
                    {faq.q}
                  </CardTitleEditorial>
                </CardHeader>
                <CardContent>
                  <p className="text-[15px] leading-relaxed text-lf-text-muted">{faq.a}</p>
                </CardContent>
              </Card>
            ))}
          </div></Reveal>
        </section>

        <section className="bg-lf-gradient-action" aria-label="Attorney review">
          <div className="mx-auto flex max-w-6xl flex-col gap-8 px-4 py-16 sm:px-6 sm:py-20 lg:flex-row lg:items-center lg:justify-between lg:gap-12 lg:px-8">
            <div className="max-w-2xl">
              <div className="mb-6 h-1 w-12 rounded-full bg-white/60" aria-hidden="true" />
              <h2 className="flex items-center gap-3 font-lf-serif text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                <ShieldCheck className="size-8 shrink-0 text-white" aria-hidden="true" />
                {ATTORNEY_PILLAR.title}
              </h2>
              <p className="mt-4 text-lg leading-relaxed text-white/90">{ATTORNEY_PILLAR.body}</p>
            </div>
            <div className="shrink-0">
              <Button size="lg" asChild className="bg-white font-semibold text-lf-violet-700 shadow-lf-card hover:bg-white/90 focus-visible:ring-lf-violet-700">
                <Link href="/login">
                  Get started <ArrowRight aria-hidden="true" />
                </Link>
              </Button>
            </div>
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}

/** Illustrative product specimen under the hero — mirrors the interview UI. */
function ProductMock(): React.JSX.Element {
  return (
    <div aria-hidden="true" className="lf-float mx-auto max-w-4xl">
      <div className="overflow-hidden rounded-lf-hero border border-lf-border bg-lf-canvas-elevated text-left shadow-lf-card">
        <div className="flex items-center gap-2 border-b border-lf-border px-4 py-2.5">
          <span className="size-2.5 rounded-full bg-[#E0527A]" />
          <span className="size-2.5 rounded-full bg-[#D97706]" />
          <span className="size-2.5 rounded-full bg-[#0EA47A]" />
          <span className="ml-3 rounded-full bg-lf-canvas-subtle px-3 py-0.5 text-[11px] text-lf-text-subtle">
            legacyflow.genalphalabs.com
          </span>
        </div>
        <div className="grid gap-5 p-5 sm:grid-cols-[1.35fr_1fr] sm:p-6">
          <div className="flex flex-col gap-3">
            <div className="max-w-[85%] rounded-2xl rounded-bl-md bg-lf-panel-violet p-3.5">
              <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-lf-violet-700">
                LegacyFlow AI
              </p>
              <p className="text-sm leading-relaxed text-lf-text">
                Who should care for your children if something happens to you both?
              </p>
            </div>
            <div className="ml-auto max-w-[80%] rounded-2xl rounded-br-md bg-lf-gradient-action p-3.5">
              <p className="text-sm leading-relaxed text-white">My brother, with my sister as backup.</p>
            </div>
            <div className="flex items-center justify-between gap-3 rounded-lf-card border border-lf-border bg-lf-canvas-elevated p-3.5 shadow-lf-subtle">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-wider text-lf-text-subtle">
                  Proposed · Guardians
                </p>
                <p className="mt-0.5 text-sm font-medium text-lf-text">
                  Brother as guardian · sister as backup
                </p>
              </div>
              <span className="flex shrink-0 items-center gap-1 rounded-full bg-lf-panel-mint px-2.5 py-1 text-[11px] font-bold text-lf-success-700">
                <Check className="size-3.5" /> Confirmed
              </span>
            </div>
          </div>
          <div className="flex items-center gap-4 rounded-lf-card bg-lf-canvas-subtle/60 p-4">
            <svg width="104" height="104" viewBox="0 0 118 118" className="shrink-0">
              <circle cx="59" cy="59" r="42" fill="none" stroke="var(--lf-border)" strokeWidth="13" />
              <circle
                cx="59" cy="59" r="42" fill="none" stroke="url(#mockring)" strokeWidth="13"
                strokeLinecap="round" strokeDasharray="264" strokeDashoffset="74"
                transform="rotate(-90 59 59)"
              />
              <defs>
                <linearGradient id="mockring" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stopColor="#22D3EE" />
                  <stop offset="0.5" stopColor="#0E9BBF" />
                  <stop offset="1" stopColor="#7C6CF0" />
                </linearGradient>
              </defs>
              <text x="59" y="58" textAnchor="middle" fontSize="20" fontWeight="800" fill="var(--lf-text)">72%</text>
              <text x="59" y="75" textAnchor="middle" fontSize="9.5" fill="var(--lf-text-muted)">complete</text>
            </svg>
            <ul className="space-y-2 text-xs">
              {[
                { dot: 'bg-lf-accent-500', name: 'Household', state: 'Complete' },
                { dot: 'bg-lf-sky-500', name: 'Guardians', state: 'Complete' },
                { dot: 'bg-lf-violet-500', name: 'Assets', state: 'In progress' },
                { dot: 'bg-lf-success-500', name: 'Wishes', state: 'Up next' },
              ].map((row) => (
                <li key={row.name} className="flex items-center gap-2">
                  <span className={`size-2 rounded-full ${row.dot}`} />
                  <span className="font-semibold text-lf-text">{row.name}</span>
                  <span className="text-lf-text-muted">{row.state}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
