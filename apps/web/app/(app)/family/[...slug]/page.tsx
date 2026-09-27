import { notFound } from 'next/navigation';
import { PlaceholderPage } from '@/components/placeholder-page';
import { findNavItem } from '@/lib/nav';

interface FamilyPageProps {
  params: Promise<{ slug: string[] }>;
}

/**
 * Family workspace routes (§5.5). Slice 1: every section renders the shared
 * placeholder page — real screens land in later slices.
 *
 * Required catch-all ([...slug], not [[...slug]]): `/family` itself is served
 * by the sibling `page.tsx` dashboard — Next.js forbids an optional catch-all
 * alongside a same-level static route.
 */
export default async function FamilySectionPage({
  params,
}: FamilyPageProps): Promise<React.JSX.Element> {
  const { slug } = await params;
  const item = findNavItem('family', slug[0] ?? '');
  if (!item) {
    notFound();
  }
  return <PlaceholderPage item={item} />;
}
