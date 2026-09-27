import { notFound, redirect } from 'next/navigation';
import { PlaceholderPage } from '@/components/placeholder-page';
import { findNavItem } from '@/lib/nav';

interface AdminPageProps {
  params: Promise<{ slug?: string[] }>;
}

/** Platform admin routes (§5.5). Slice 1: shared placeholder pages. */
export default async function AdminSectionPage({
  params,
}: AdminPageProps): Promise<React.JSX.Element> {
  const { slug } = await params;
  if (!slug || slug.length === 0) {
    redirect('/admin/firms');
  }
  const item = findNavItem('admin', slug[0] ?? '');
  if (!item) {
    notFound();
  }
  return <PlaceholderPage item={item} />;
}
