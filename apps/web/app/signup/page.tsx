import type { Metadata } from 'next';
import { FileCheck2, Save } from 'lucide-react';
import { SignUpForm } from '@/components/signup-form';
import { PRODUCT_NAME } from '@/lib/constants';

export const metadata: Metadata = {
  title: 'Create account — LegacyFlow',
  description: 'Create a LegacyFlow account with your email and a password.',
};

/**
 * Sign-up page — split layout: editorial panel (ink) beside the form card.
 * All auth logic lives in {@link SignUpForm}; this file is layout only.
 */
export default function SignUpPage(): React.JSX.Element {
  return (
    <main id="main-content" className="grid min-h-screen bg-lf-canvas lg:grid-cols-2">
      <div className="order-1 flex items-center justify-center px-4 py-12 sm:px-6 lg:order-2 lg:px-12">
        <SignUpForm />
      </div>

      <div className="order-2 bg-lf-primary-900 lg:order-1">
        <div className="mx-auto flex h-full max-w-md flex-col justify-center px-6 py-12 sm:py-16 lg:max-w-lg lg:px-12">
          <p className="font-lf-serif text-xl font-semibold tracking-tight text-white">
            {PRODUCT_NAME}
          </p>
          <h1 className="mt-10 font-lf-serif text-4xl font-semibold leading-[1.1] tracking-tight text-white lg:text-[44px]">
            Start your family&rsquo;s plan.
          </h1>
          <p className="mt-4 text-[15px] leading-relaxed text-white/75">
            A calm, guided way to organize the people, assets, and wishes that make up your
            plan.
          </p>
          <ul className="mt-10 space-y-5">
            <li className="flex items-start gap-3">
              <Save className="mt-0.5 size-5 shrink-0 text-lf-accent-500" aria-hidden="true" />
              <p className="text-sm leading-relaxed text-white/80">
                Answer at your own pace — your answers save automatically as you go.
              </p>
            </li>
            <li className="flex items-start gap-3">
              <FileCheck2 className="mt-0.5 size-5 shrink-0 text-lf-accent-500" aria-hidden="true" />
              <p className="text-sm leading-relaxed text-white/80">
                A licensed attorney reviews everything before it counts.
              </p>
            </li>
          </ul>
        </div>
      </div>
    </main>
  );
}
