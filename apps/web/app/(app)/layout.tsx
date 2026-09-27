import { redirect } from 'next/navigation';
import { getServerSession } from '@/lib/auth/server';
import { AppShell } from '@/components/app-shell';

/** Authenticated app shell — all /family, /firm, /admin routes render inside. */
export default async function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}): Promise<React.JSX.Element> {
  const session = await getServerSession();
  if (!session) {
    redirect('/login');
  }
  return <AppShell user={session.user}>{children}</AppShell>;
}
