import Link from 'next/link';
import { ArrowRight, FileCheck2, ShieldCheck, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitleEditorial } from '@/components/ui/card';
import { Footer } from '@/components/footer';
import { PRODUCT_NAME, SAFETY_LABEL } from '@/lib/constants';

const PILLARS = [
  {
    icon: Users,
    title: 'Built for families',
    body: 'A calm, guided way to organize the people, assets, and wishes that make up your plan.',
  },
  {
    icon: FileCheck2,
    title: 'Attorney-guided',
    body: 'Every plan is reviewed by a licensed attorney before anything is finalized. Nothing ships on autopilot.',
  },
  {
    icon: ShieldCheck,
    title: 'Private by design',
    body: 'Your information stays yours. Access is controlled, audited, and limited to your firm.',
  },
] as const;

const ATTORNEY_PILLAR = PILLARS[1];

/** Public landing page — placeholder marketing copy, safety-labeled. */
export default function LandingPage(): React.JSX.Element {
  return (
    <div className="flex min-h-screen flex-col bg-lf-canvas">
      <header className="border-b border-lf-border bg-lf-canvas">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <p className="font-lf-serif text-xl font-semibold tracking-tight text-lf-text">
            {PRODUCT_NAME}
          </p>
          <nav className="flex items-center gap-2" aria-label="Primary">
            <Button variant="ghost" asChild>
              <Link href="/about">About</Link>
            </Button>
            <Button asChild>
              <Link href="/login">Sign in</Link>
            </Button>
          </nav>
        </div>
      </header>

      <main id="main-content" className="flex-1">
        <section className="mx-auto max-w-6xl px-4 py-16 text-center sm:px-6 sm:py-24 lg:px-8">
          <div className="mx-auto mb-8 h-px w-16 bg-lf-gold" aria-hidden="true" />
          <h1 className="mx-auto max-w-4xl font-lf-serif text-[40px] font-semibold leading-[1.08] tracking-tight text-lf-text sm:text-[56px] lg:text-[64px]">
            Estate planning your family can actually finish
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-lf-text-muted">
            Organize your household, guardians, and assets step by step — with a licensed attorney
            reviewing everything before it counts.
          </p>
          <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button size="lg" variant="accent" asChild>
              <Link href="/login">
                Get started <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
            <Button size="lg" variant="outline" asChild>
              <Link href="/about">How it works</Link>
            </Button>
          </div>
          <p className="mx-auto mt-8 max-w-xl text-xs leading-relaxed text-lf-text-subtle" role="note">
            {SAFETY_LABEL}
          </p>
        </section>

        <section
          className="mx-auto max-w-6xl px-4 pb-16 sm:px-6 sm:pb-24 lg:px-8"
          aria-label="Why LegacyFlow"
        >
          <div className="grid gap-6 md:grid-cols-3">
            {PILLARS.map((pillar) => (
              <Card key={pillar.title} className="bg-white">
                <CardHeader>
                  <span className="flex size-11 items-center justify-center rounded-lf-control bg-lf-accent-600/10 text-lf-accent-700">
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

        <section className="bg-lf-primary-900" aria-label="Attorney review">
          <div className="mx-auto flex max-w-6xl flex-col gap-8 px-4 py-16 sm:px-6 sm:py-20 lg:flex-row lg:items-center lg:justify-between lg:gap-12 lg:px-8">
            <div className="max-w-2xl">
              <div className="mb-6 h-px w-12 bg-lf-gold" aria-hidden="true" />
              <h2 className="flex items-center gap-3 font-lf-serif text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                <ShieldCheck className="size-8 shrink-0 text-lf-gold" aria-hidden="true" />
                {ATTORNEY_PILLAR.title}
              </h2>
              <p className="mt-4 text-lg leading-relaxed text-white/80">{ATTORNEY_PILLAR.body}</p>
            </div>
            <div className="shrink-0">
              <Button size="lg" variant="accent" asChild>
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
