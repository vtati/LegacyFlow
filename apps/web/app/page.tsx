import Link from 'next/link';
import { ArrowRight, FileCheck2, ShieldCheck, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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

/** Public landing page — placeholder marketing copy, safety-labeled. */
export default function LandingPage(): React.JSX.Element {
  return (
    <div className="flex min-h-screen flex-col bg-lf-canvas">
      <header className="border-b border-lf-border bg-lf-canvas-elevated">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
          <p className="text-lg font-bold tracking-tight text-lf-primary-950">{PRODUCT_NAME}</p>
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
        <section className="mx-auto max-w-6xl px-4 py-16 text-center sm:py-24">
          <h1 className="mx-auto max-w-3xl font-lf-serif text-4xl font-bold tracking-tight text-lf-primary-950 sm:text-5xl">
            Estate planning your family can actually finish
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-lf-text-muted">
            Organize your household, guardians, and assets step by step — with a licensed attorney
            reviewing everything before it counts.
          </p>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button size="lg" asChild>
              <Link href="/login">
                Get started <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
            <Button size="lg" variant="outline" asChild>
              <Link href="/about">How it works</Link>
            </Button>
          </div>
          <p className="mt-6 text-xs text-lf-text-subtle" role="note">
            {SAFETY_LABEL}
          </p>
        </section>

        <section className="mx-auto max-w-6xl px-4 pb-16" aria-label="Why LegacyFlow">
          <div className="grid gap-4 md:grid-cols-3">
            {PILLARS.map((pillar) => (
              <Card key={pillar.title}>
                <CardHeader>
                  <span className="flex size-10 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
                    <pillar.icon className="size-5" aria-hidden="true" />
                  </span>
                  <CardTitle>{pillar.title}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-lf-text-muted">{pillar.body}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}
