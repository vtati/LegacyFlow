import type { Metadata } from 'next';
import { LoginForm } from '@/components/login-form';

export const metadata: Metadata = {
  title: 'Sign in — LegacyFlow',
  description: 'Sign in to LegacyFlow with Google or a magic link.',
};

export default function LoginPage(): React.JSX.Element {
  return (
    <main
      id="main-content"
      className="flex min-h-screen items-center justify-center bg-lf-canvas px-4 py-12"
    >
      <LoginForm />
    </main>
  );
}
