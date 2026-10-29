import type { Metadata } from 'next';
import { Lock, ShieldCheck } from 'lucide-react';
import { LoginForm } from '@/components/login-form';
import { PRODUCT_NAME } from '@/lib/constants';

export const metadata: Metadata = {
  title: 'Sign in — LegacyFlow',
  description: 'Sign in to LegacyFlow with Google, your password, or a magic link.',
};

/**
 * Sign-in page — split layout: editorial panel (ink) beside the form card.
 * All auth logic lives in {@link LoginForm}; this file is layout only.
 */
export default function LoginPage(): React.JSX.Element {
  return (
    <main id="main-content" className="grid min-h-screen bg-lf-canvas lg:grid-cols-2">
      <div className="order-1 flex items-center justify-center px-4 py-12 sm:px-6 lg:order-2 lg:px-12">
        <LoginForm />
      </div>

      <div className="order-2 bg-lf-primary-900 lg:order-1">
        <div className="mx-auto flex h-full max-w-md flex-col justify-center px-6 py-12 sm:py-16 lg:max-w-lg lg:px-12">
          <p className="font-lf-serif text-xl font-semibold tracking-tight text-white">
            {PRODUCT_NAME}
          </p>
          {/* Editorial slogan is a subheading (UI-003): the form column's
              "Sign in to LegacyFlow" is the page's h1. */}
          <h2 className="mt-10 font-lf-serif text-4xl font-semibold leading-[1.1] tracking-tight text-white lg:text-[44px]">
            Pick up where you left off.
          </h2>
          <p className="mt-4 text-[15px] leading-relaxed text-white/75">
            Your plan waits exactly as you left it — every answer saved, every document in
            place.
          </p>
          <ul className="mt-10 space-y-5">
            <li className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 size-5 shrink-0 text-lf-accent-500" aria-hidden="true" />
              <p className="text-sm leading-relaxed text-white/80">
                Your information stays yours. Access is controlled, audited, and limited to
                your firm.
              </p>
            </li>
            <li className="flex items-start gap-3">
              <Lock className="mt-0.5 size-5 shrink-0 text-lf-accent-500" aria-hidden="true" />
              <p className="text-sm leading-relaxed text-white/80">
                Nothing is finalized without attorney review — nothing ships on autopilot.
              </p>
            </li>
          </ul>
        </div>
      </div>
    </main>
  );
}
