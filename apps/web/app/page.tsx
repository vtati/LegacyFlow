import { redirect } from 'next/navigation';
import Link from 'next/link';
import { ArrowRight, FileCheck2, ShieldCheck, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitleEditorial } from '@/components/ui/card';
import { Footer } from '@/components/footer';
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
function LandingPage(): React.JSX.Element {
  return (
    <div className="flex min-h-screen flex-col bg-lf-canvas">
      <header className="border-b border-lf-border bg-lf-canvas">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-2.5">
            <img
              src="/legacyflow-logo.webp"
              alt="LegacyFlow logo"
              className="size-9 rounded-lg"
            />
            <p className="font-lf-serif text-xl font-semibold tracking-tight text-lf-text">
              {PRODUCT_NAME}
            </p>
          </div>
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
              <Link href="#how-it-works">How it works</Link>
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

        <section
          id="how-it-works"
          className="mx-auto max-w-6xl scroll-mt-20 px-4 pb-16 sm:px-6 sm:pb-24 lg:px-8"
          aria-label="How it works"
        >
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
              <Card key={item.step} className="bg-white">
                <CardHeader>
                  <span className="flex size-11 items-center justify-center rounded-full bg-lf-primary-900 font-lf-serif text-lg font-semibold text-white">
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
        </section>

        <section
          className="mx-auto max-w-4xl px-4 pb-16 sm:px-6 sm:pb-24 lg:px-8"
          aria-label="Frequently asked questions"
        >
          <div className="mb-10 text-center">
            <h2 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text sm:text-4xl">
              Common questions
            </h2>
          </div>
          <div className="space-y-4">
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
              <Card key={faq.q} className="bg-white">
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
