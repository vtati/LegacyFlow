import { Construction } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import type { NavItem } from '@/lib/nav';

interface PlaceholderPageProps {
  item: NavItem;
}

/**
 * Shared empty-state page for Slice 1 routes.
 * One component backs every family/firm/admin placeholder route —
 * no legal content, no real data, clearly labeled as a future slice.
 */
export function PlaceholderPage({ item }: PlaceholderPageProps): React.JSX.Element {
  const Icon = item.icon;
  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
              <Icon className="size-5" aria-hidden="true" />
            </span>
            <div>
              <CardTitle>{item.label}</CardTitle>
              <CardDescription>{item.blurb}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center gap-3 rounded-lf-control border border-dashed border-lf-border bg-lf-canvas-subtle px-6 py-10 text-center">
            <Construction className="size-8 text-lf-text-subtle" aria-hidden="true" />
            <p className="text-sm font-medium text-lf-text">Coming in a later slice</p>
            <p className="max-w-md text-sm text-lf-text-muted">
              This area is planned but not built yet. Placeholder copy only — attorney-pending, not
              legal advice. Synthetic data only.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
