import { notFound, redirect } from 'next/navigation';
import { PlaceholderPage } from '@/components/placeholder-page';
import { findNavItem } from '@/lib/nav';

interface FirmPageProps {
  params: Promise<{ slug?: string[] }>;
}

/** Firm workspace routes (§5.5). Slice 1: shared placeholder pages. */
export default async function FirmSectionPage({
  params,
}: FirmPageProps): Promise<React.JSX.Element> {
  const { slug } = await params;
  if (!slug || slug.length === 0) {
    redirect('/firm/overview');
  }
  const item = findNavItem('firm', slug[0] ?? '');
  if (!item) {
    notFound();
  }
  return <PlaceholderPage item={item} />;
}
