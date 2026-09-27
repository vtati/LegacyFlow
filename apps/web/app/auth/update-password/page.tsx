import type { Metadata } from 'next';
import { UpdatePasswordForm } from '@/components/update-password-form';

export const metadata: Metadata = {
  title: 'Choose a new password — LegacyFlow',
  description: 'Set a new LegacyFlow password from a reset link.',
};

export default function UpdatePasswordPage(): React.JSX.Element {
  return (
    <main
      id="main-content"
      className="flex min-h-screen items-center justify-center bg-lf-canvas px-4 py-12"
    >
      <UpdatePasswordForm />
    </main>
  );
}
