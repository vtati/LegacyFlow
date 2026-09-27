import type { Metadata } from 'next';
import { SignUpForm } from '@/components/signup-form';

export const metadata: Metadata = {
  title: 'Create account — LegacyFlow',
  description: 'Create a LegacyFlow account with your email and a password.',
};

export default function SignUpPage(): React.JSX.Element {
  return (
    <main
      id="main-content"
      className="flex min-h-screen items-center justify-center bg-lf-canvas px-4 py-12"
    >
      <SignUpForm />
    </main>
  );
}
