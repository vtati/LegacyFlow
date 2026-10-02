import type { Metadata } from 'next';
import { MailCheck } from 'lucide-react';
import { ResetPasswordForm } from '@/components/reset-password-form';
import { PRODUCT_NAME } from '@/lib/constants';

export const metadata: Metadata = {
  title: 'Reset password — LegacyFlow',
  description: 'Request a LegacyFlow password reset link.',
};

/**
 * Password-reset request page — split layout: editorial panel (ink) beside the
 * form card. All auth logic lives in {@link ResetPasswordForm}; layout only.
 */
export default function ResetPasswordPage(): React.JSX.Element {
  return (
    <main id="main-content" className="grid min-h-screen bg-lf-canvas lg:grid-cols-2">
      <div className="order-1 flex items-center justify-center px-4 py-12 sm:px-6 lg:order-2 lg:px-12">
        <ResetPasswordForm />
      </div>

      <div className="order-2 bg-lf-primary-900 lg:order-1">
        <div className="mx-auto flex h-full max-w-md flex-col justify-center px-6 py-12 sm:py-16 lg:max-w-lg lg:px-12">
          <p className="font-lf-serif text-xl font-semibold tracking-tight text-white">
            {PRODUCT_NAME}
          </p>
          <h1 className="mt-10 font-lf-serif text-4xl font-semibold leading-[1.1] tracking-tight text-white lg:text-[44px]">
            We&rsquo;ll get you back in.
          </h1>
          <ul className="mt-10 space-y-5">
            <li className="flex items-start gap-3">
              <MailCheck className="mt-0.5 size-5 shrink-0 text-lf-accent-500" aria-hidden="true" />
              <p className="text-sm leading-relaxed text-white/80">
                Enter your email and we&rsquo;ll send a link to choose a new password. Your
                plan and documents will be right where you left them.
              </p>
            </li>
          </ul>
        </div>
      </div>
    </main>
  );
}
