'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { PRODUCT_NAME } from '@/lib/constants';
import { SECTION_TITLES, type AppSection } from '@/lib/nav';
import type { AuthUser } from '@/lib/auth/adapter';
import { AccountMenu } from '@/components/account-menu';
import { MobileNav } from '@/components/mobile-nav';
import { SaveIndicator } from '@/components/save-indicator';
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
          <div className="flex items-center gap-1">
            <MobileNav section={section} />
            <Link
              href="/family/home"
              className="rounded-lf-control font-lf-serif text-lg font-semibold tracking-tight text-lf-primary-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
            >
              {PRODUCT_NAME}
            </Link>
          </div>
          <div className="flex items-center gap-2 sm:gap-4">
            {/* Real save state: reflects actual persistence via the save-status bus. */}
            <SaveIndicator />
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

      {/* Mobile nav lives in the header hamburger (see MobileNav) — nothing stacked here. */}
      <Footer />
    </div>
  );
}
