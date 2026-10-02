import Link from 'next/link';
import type { Metadata } from 'next';
import { ArrowLeft, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Footer } from '@/components/footer';
import { PRODUCT_NAME } from '@/lib/constants';

export const metadata: Metadata = {
  title: 'About',
  description: 'What LegacyFlow is — and the limits of this demo scaffold.',
};

/** Safety statements surfaced from §2.3 of the product artifacts. */
const SAFETY_POINTS = [
  'The platform never claims a document is legally valid.',
  'The platform never states you have received legal advice unless it was delivered by an attorney through the firm’s controlled workflow.',
  'The platform records signing checklist completion and your attestation; it does not certify legal execution.',
  'Legal content is shown only when its source, version, and jurisdiction scope can be established.',
  'If approved legal content is missing, the platform escalates safely instead of generating an answer.',
] as const;

export default function AboutPage(): React.JSX.Element {
  return (
    <div className="flex min-h-screen flex-col bg-lf-canvas">
      <header className="border-b border-lf-border bg-lf-canvas">
        <div className="mx-auto flex h-16 max-w-4xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <p className="font-lf-serif text-xl font-semibold tracking-tight text-lf-text">
            {PRODUCT_NAME}
          </p>
          <Button variant="ghost" asChild>
            <Link href="/">
              <ArrowLeft aria-hidden="true" /> Back home
            </Link>
          </Button>
        </div>
      </header>

      <main id="main-content" className="mx-auto w-full max-w-4xl flex-1 px-4 py-12 sm:px-6 sm:py-16 lg:px-8">
        <h1 className="font-lf-serif text-4xl font-semibold tracking-tight text-lf-text">
          About this demo
        </h1>
        <p className="mt-4 max-w-2xl text-lg leading-relaxed text-lf-text-muted">
          {PRODUCT_NAME} is an estate-planning platform built around attorney review. What you see
          here is an early scaffold: screens, navigation, and flows are real, but the legal content
          behind them is not.
        </p>

        <Card className="mt-10 bg-white">
          <CardHeader>
            <div className="flex items-center gap-3">
              <span className="flex size-11 items-center justify-center rounded-lf-control bg-lf-accent-600/10 text-lf-accent-700">
                <ShieldAlert className="size-5" aria-hidden="true" />
              </span>
              <CardTitle className="font-lf-serif text-xl font-semibold">
                What this demo is — and isn’t
              </CardTitle>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <ul className="flex list-disc flex-col gap-2 pl-5 text-sm text-lf-text">
              {SAFETY_POINTS.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
            <p
              className="rounded-lf-control bg-lf-canvas-subtle px-4 py-3 text-sm text-lf-text-muted"
              role="note"
            >
              <strong className="text-lf-text">Bottom line:</strong> nothing in this demo is legal
              advice, and no document shown here is legally valid. All sample content is synthetic
              and clearly labeled.
            </p>
          </CardContent>
        </Card>

        <div className="mt-8 flex gap-3">
          <Button asChild>
            <Link href="/login">Sign in to explore</Link>
          </Button>
          <Button variant="outline" asChild>
            <Link href="/">Back to home</Link>
          </Button>
        </div>
      </main>

      <Footer />
    </div>
  );
}
