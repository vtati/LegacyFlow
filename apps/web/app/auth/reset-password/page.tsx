import type { Metadata } from 'next';
import { ResetPasswordForm } from '@/components/reset-password-form';

export const metadata: Metadata = {
  title: 'Reset password — LegacyFlow',
  description: 'Request a LegacyFlow password reset link.',
};

export default function ResetPasswordPage(): React.JSX.Element {
  return (
    <main
      id="main-content"
      className="flex min-h-screen items-center justify-center bg-lf-canvas px-4 py-12"
    >
      <ResetPasswordForm />
    </main>
  );
}
