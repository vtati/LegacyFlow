import { IntakeWizard } from '@/components/family/intake/intake-wizard';

interface IntakeMatterPageProps {
  params: Promise<{ matterId: string }>;
}

/** Guided intake wizard for one matter. */
export default async function IntakeMatterPage({
  params,
}: IntakeMatterPageProps): Promise<React.JSX.Element> {
  const { matterId } = await params;
  // Keyed by matter (H2): navigating between two matters' intakes
  // reuses this page component in the App Router, and the wizard's
  // per-matter state (verdict, submitted, progress) must never carry
  // across — a blocking verdict on one matter would otherwise lock
  // the next one read-only.
  return <IntakeWizard key={matterId} matterId={matterId} />;
}
