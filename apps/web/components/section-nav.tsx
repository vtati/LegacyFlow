'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { SECTION_NAV, type AppSection } from '@/lib/nav';
import { cn } from '@/lib/utils';

interface SectionNavProps {
  section: AppSection;
}

/** Sidebar navigation for one app section (§5.5). Renders every nav item. */
export function SectionNav({ section }: SectionNavProps): React.JSX.Element {
  const pathname = usePathname();
  const items = SECTION_NAV[section];

  return (
    <nav aria-label={`${section} navigation`}>
      <ul className="flex flex-col gap-1">
        {items.map((item) => {
          const active = pathname === item.href;
          const Icon = item.icon;
          return (
            <li key={item.slug}>
              <Link
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'flex items-center gap-3 rounded-lf-control px-3 py-2 text-sm font-medium transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500',
                  active
                    ? 'bg-lf-primary-800 text-lf-primary-foreground'
                    : 'text-lf-text-muted hover:bg-lf-primary-50 hover:text-lf-primary-900',
                )}
              >
                <Icon className="size-4 shrink-0" aria-hidden="true" />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
