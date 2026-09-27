'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { CheckCheck } from 'lucide-react';
import { PRODUCT_NAME } from '@/lib/constants';
import { SECTION_TITLES, type AppSection } from '@/lib/nav';
import type { AuthUser } from '@/lib/auth/adapter';
import { AccountMenu } from '@/components/account-menu';
import { SectionNav } from '@/components/section-nav';
import { Footer } from '@/components/footer';

interface AppShellProps {
  user: AuthUser;
  children: React.ReactNode;
}

function sectionFromPath(pathname: string): AppSection {
  if (pathname.startsWith('/firm')) return 'firm';
  if (pathname.startsWith('/admin')) return 'admin';
  return 'family';
}

/** Authenticated app shell: header, sidebar nav, content, safety-labeled footer. */
export function AppShell({ user, children }: AppShellProps): React.JSX.Element {
  const pathname = usePathname();
  const section = sectionFromPath(pathname);

  return (
    <div className="flex min-h-screen flex-col bg-lf-canvas">
      <header className="sticky top-0 z-40 border-b border-lf-border bg-lf-canvas-elevated">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4">
          <Link
            href="/family/home"
            className="rounded-lf-control text-lg font-bold tracking-tight text-lf-primary-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            {PRODUCT_NAME}
          </Link>
          <div className="flex items-center gap-2 sm:gap-4">
            {/* Saved-state placeholder — wired to real persistence in a later slice. */}
            <p
              className="hidden items-center gap-1.5 text-xs text-lf-text-subtle md:flex"
              role="status"
              aria-label="Saved state"
            >
              <CheckCheck className="size-4 text-lf-success-700" aria-hidden="true" />
              All changes saved
            </p>
            <AccountMenu user={user} />
          </div>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-7xl flex-1 gap-6 px-4 py-6">
        <aside className="hidden w-60 shrink-0 md:block">
          <div className="sticky top-24">
            <p className="mb-3 px-3 text-xs font-semibold uppercase tracking-wider text-lf-text-subtle">
              {SECTION_TITLES[section]}
            </p>
            <SectionNav section={section} />
          </div>
        </aside>
        <main className="min-w-0 flex-1" id="main-content">
          {children}
        </main>
      </div>

      {/* Mobile: horizontal nav under the header (no permanent sidebar on small screens). */}
      <nav
        aria-label={`${section} navigation (mobile)`}
        className="border-t border-lf-border bg-lf-canvas-elevated px-4 py-2 md:hidden"
      >
        <SectionNav section={section} />
      </nav>

      <Footer />
    </div>
  );
}
