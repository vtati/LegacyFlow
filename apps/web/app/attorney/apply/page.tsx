import type { Metadata } from 'next';
import { AttorneyApplyForm } from '@/components/attorney/attorney-apply-form';

export const metadata: Metadata = {
  title: 'Apply as an attorney — LegacyFlow',
  description: 'Join the LegacyFlow attorney network. Submit your credentials for verification.',
};

/** Public attorney application page (requires sign-in). */
export default function AttorneyApplyPage(): React.JSX.Element {
  return (
    <main id="main-content" className="min-h-screen bg-lf-canvas">
      <div className="mx-auto max-w-2xl px-4 py-12 sm:px-6">
        <div className="lf-animate-fade-up">
          <p className="text-sm font-semibold uppercase tracking-wider text-lf-primary-600">
            Attorney network
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-lf-text">
            Apply to join LegacyFlow
          </h1>
          <p className="mt-3 text-[15px] leading-relaxed text-lf-text-muted">
            Review estate plans for families in your jurisdictions. Applications are
            verified manually — provide your bar credentials and supporting documents
            and our team will review them.
          </p>
        </div>
        <div className="mt-8 lf-animate-fade-up">
          <AttorneyApplyForm />
        </div>
      </div>
    </main>
  );
}
