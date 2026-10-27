'use client';

import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { ParkedItem } from '@/lib/api-client';

/**
 * Parked open items (Phase 4, P4): questions the family set aside
 * with "don't know yet" during the interview. They still gate draft
 * generation, so they surface here — with the label the documents
 * will use and a path back into the interview, where answering (or
 * un-parking) resolves the item. Renders nothing when none are open:
 * the endpoint already intersected markers with the applicable
 * partition server-side, so this list is never stale.
 */
export function ReviewParkedItems({
  matterId,
  items,
}: {
  matterId: string;
  items: ParkedItem[];
}): React.JSX.Element | null {
  if (items.length === 0) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Saved for later — still to finish</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-lf-text-muted">
          During your interview you set{' '}
          {items.length === 1 ? 'this question' : 'these questions'} aside with
          &ldquo;don&rsquo;t know yet&rdquo;. Your documents can&rsquo;t be created until each
          one is answered.
        </p>
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.question_key}
              className="flex flex-wrap items-center justify-between gap-3"
            >
              <span className="text-sm text-lf-text">{item.label}</span>
              <Button asChild variant="outline" size="sm">
                <Link href={`/family/interview/${matterId}`}>Answer in your interview</Link>
              </Button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
